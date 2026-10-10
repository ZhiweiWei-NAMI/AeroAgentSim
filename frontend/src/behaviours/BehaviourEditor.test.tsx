import { useState } from 'react';
import { fireEvent, render, screen, within } from '@testing-library/react';
import { beforeEach, expect, it, vi } from 'vitest';
import { I18nProvider } from '../i18n/I18nProvider';
import { Ast, BehaviourEditor, removeAuthoredElement } from './BehaviourEditor';
import { applicableFields, applicableRelations, patch, type Draft } from './model';
const draft: Draft = { format: 'aeroagentsim.behaviour-package/v1', id: 'example', revision: 1, unknown: { enabled: false, value: null }, predicates: { ready: { profile: 'committed_reactive/v1', roles: { task: 'child' }, expression: { op: 'eq', args: [{ field: 'phase', role: 'task', path: [] }, { literal: 'queued' }] } } }, chains: { response: { roles: { task: 'child' }, trigger: { predicate: 'ready', edge: 'entered' }, initial: 'waiting', terminal: ['done'], states: ['waiting', 'done'], transitions: [{ id: 'launch', from: 'waiting', on: { instance: 'activated' }, guard: 'ready', to: 'done', actions: [], unknownTransition: { keep: true } }] } }, bindings: [], conflicts: [], injection_points: [] };
beforeEach(() => window.localStorage.setItem('aeroagentsim.locale', 'en-US'));
function Harness({ initial = draft, initialTab, validity, onValidate }: { initial?: Draft; initialTab?: string; validity?: (valid: boolean) => void; onValidate?: () => void }) {
  const [value, setValue] = useState(initial);
  return <I18nProvider><BehaviourEditor value={value} onChange={setValue} initialTab={initialTab} onValidityChange={validity} onValidate={onValidate}/><pre data-testid="model">{JSON.stringify(value)}</pre></I18nProvider>;
}
function chainDraft(changes: Draft): Draft {
  return patch(draft, ['chains', 'response'], { ...(draft.chains as Draft).response as Draft, ...changes }) as Draft;
}

it('shares state-machine graph, transition forms and raw model while retaining unsupported content', () => {
  render(<Harness/>); fireEvent.click(screen.getByRole('button', { name: 'Event chains' }));
  expect(screen.getByLabelText('Graph transition launch')).toBeTruthy(); fireEvent.change(screen.getByLabelText('Transition 0 id'), { target: { value: 'finish' } });
  expect(screen.getByLabelText('Graph transition finish')).toBeTruthy(); expect(screen.getByTestId('model')).toHaveTextContent('unknownTransition');
  fireEvent.click(screen.getByRole('button', { name: 'Source JSON & YAML' })); fireEvent.click(screen.getByTestId('details-trigger'));
  const raw = screen.getByLabelText('Behaviour package JSON') as HTMLTextAreaElement;
  expect(JSON.parse(raw.value).unknown).toEqual({ enabled: false, value: null });
});

it('preserves unsupported AST operators and filters inherited fields by real ancestry', () => {
  const authored = { ...draft, predicates: { ready: { profile: 'committed_reactive/v1', roles: {}, expression: { op: 'futureDialect', extra: { keep: true } } } } };
  render(<I18nProvider><BehaviourEditor value={authored} onChange={() => {}}/></I18nProvider>);
  expect(screen.getByText(/Unsupported AST content retained/)).toBeTruthy();
  expect(applicableFields('child', [{ id: 'child', parents: ['parent'], abstract: false }, { id: 'parent', parents: [], abstract: true }], [{ id: 'inherited', type: 'parent' }, { id: 'other', type: 'unrelated' }])).toEqual([{ id: 'inherited', type: 'parent' }]);
  expect(applicableRelations('child', 'child', [{ id: 'child', parents: ['parent'], abstract: false }], [{ id: 'actual', source_type: 'parent', target_type: 'parent' }, { id: 'opposite', source_type: 'other', target_type: 'parent' }])).toEqual([{ id: 'actual', source_type: 'parent', target_type: 'parent' }]);
  expect((patch(draft, ['chains', 'response', 'initial'], 'done') as Draft).unknown).toEqual(draft.unknown);
});

it('blocks compiler validation while a raw JSON buffer is invalid', () => {
  const validity = vi.fn(); render(<Harness validity={validity} onValidate={vi.fn()}/>);
  fireEvent.click(screen.getByRole('button', { name: 'Source JSON & YAML' })); fireEvent.click(screen.getByTestId('details-trigger'));
  fireEvent.change(screen.getByLabelText('Behaviour package JSON'), { target: { value: '{"id":' } });
  expect(screen.getByRole('button', { name: 'Validate package on server' })).toBeDisabled();
  expect(validity).toHaveBeenLastCalledWith(false);
});

it('blocks compiler validation for trigger JSON buffers and recovers on valid input', () => {
  const validity = vi.fn();
  render(<Harness initial={chainDraft({ trigger: {} })} initialTab="chains" validity={validity} onValidate={vi.fn()}/>);
  const trigger = screen.getByRole('group', { name: 'chains.response.trigger' });
  fireEvent.click(within(trigger).getByTestId('details-trigger'));
  fireEvent.change(screen.getByLabelText('chains.response.trigger trigger JSON'), { target: { value: '{"event":' } });
  expect(screen.getByRole('button', { name: 'Validate package on server' })).toBeDisabled();
  expect(validity).toHaveBeenLastCalledWith(false);
  expect(JSON.parse(screen.getByTestId('model').textContent!).chains.response.trigger).toEqual({});
  fireEvent.change(screen.getByLabelText('chains.response.trigger trigger JSON'), { target: { value: '{"event":"incident"}' } });
  expect(screen.getByRole('button', { name: 'Validate package on server' })).toBeEnabled();
  expect(validity).toHaveBeenLastCalledWith(true);
  expect(JSON.parse(screen.getByTestId('model').textContent!).chains.response.trigger).toEqual({ event: 'incident' });
});

it('blocks validation for trigger nanoseconds and expression-number buffers without changing the model', () => {
  const validity = vi.fn();
  render(<Harness initial={chainDraft({ trigger: { timer: 'start', after_ns: 1 }, preconditions: [2] })} initialTab="chains" validity={validity} onValidate={vi.fn()}/>);
  const timer = screen.getByLabelText('chains.response.trigger after_ns (exact integer nanoseconds)');
  fireEvent.change(timer, { target: { value: '1.5' } });
  expect(screen.getByRole('button', { name: 'Validate package on server' })).toBeDisabled();
  expect(JSON.parse(screen.getByTestId('model').textContent!).chains.response.trigger.after_ns).toBe(1);
  fireEvent.change(timer, { target: { value: '3' } });
  expect(screen.getByRole('button', { name: 'Validate package on server' })).toBeEnabled();
  fireEvent.change(screen.getByLabelText('Chain preconditions[0]'), { target: { value: '2e' } });
  expect(screen.getByRole('button', { name: 'Validate package on server' })).toBeDisabled();
  expect(validity).toHaveBeenLastCalledWith(false);
  expect(JSON.parse(screen.getByTestId('model').textContent!).chains.response.preconditions).toEqual([2]);
  fireEvent.change(screen.getByLabelText('Chain preconditions[0]'), { target: { value: '4' } });
  expect(screen.getByRole('button', { name: 'Validate package on server' })).toBeEnabled();
});

it('connects graph states using the keyboard and preserves existing transition extensions', () => {
  render(<Harness initialTab="chains"/>);
  fireEvent.click(screen.getByRole('checkbox', { name: 'Connect states · choose source' }));
  fireEvent.keyDown(screen.getByLabelText('Graph state waiting'), { key: 'Enter' });
  expect(screen.getByRole('checkbox', { name: 'Connect states · choose target' })).toBeChecked();
  fireEvent.keyDown(screen.getByLabelText('Graph state done'), { key: ' ' });
  const transitions = JSON.parse(screen.getByTestId('model').textContent!).chains.response.transitions;
  expect(transitions[0].unknownTransition).toEqual({ keep: true });
  expect(transitions[1]).toMatchObject({ id: 'transition-2', from: 'waiting', to: 'done' });
});

it('keeps graph links aligned with original transition indexes when unsupported entries are retained', () => {
  const launch = ((draft.chains as Draft).response as Draft).transitions as unknown[];
  render(<Harness initial={chainDraft({ transitions: [null, ...launch] })} initialTab="chains"/>);
  const transition = document.getElementById('transition-response-1')!;
  const scroll = vi.fn(); transition.scrollIntoView = scroll;
  fireEvent.keyDown(screen.getByLabelText('Graph transition launch'), { key: 'Enter' });
  expect(scroll).toHaveBeenCalledOnce();
});

it('removes a chain and only its start rules while retaining other package data', () => {
  const initial: Draft = { ...draft, bindings: [{ id: 'response-start', chain: 'response' }, { id: 'other-start', chain: 'other' }, null] };
  const removed = removeAuthoredElement(initial, 'chains', 'response');
  expect(removed.chains).toEqual({});
  expect(removed.bindings).toEqual([{ id: 'other-start', chain: 'other' }, null]);
  expect(removed.predicates).toEqual(draft.predicates);
  expect(removed.unknown).toEqual(draft.unknown);
  expect((initial.chains as Draft).response).toBeTruthy();
  render(<Harness initial={initial} initialTab="chains" />);
  expect(screen.getByRole('button', { name: 'Remove chain' })).toBeEnabled();
  expect(screen.queryByRole('button', { name: 'Validate package on server' })).toBeNull();
});

it('emits one update when an operator changes', () => {
  const change = vi.fn();
  render(<I18nProvider><Ast node={{ op: 'eq', args: [{ literal: 1 }, { literal: 2 }] }} onChange={change} path="expression" roles={{}} types={[]} fields={[]}/></I18nProvider>);
  fireEvent.change(screen.getByLabelText('expression operator'), { target: { value: 'not' } });
  expect(change).toHaveBeenCalledOnce();
  expect(change).toHaveBeenLastCalledWith({ op: 'not', args: [{ literal: 1 }] });
});
