import { fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { I18nProvider } from '../../i18n/I18nProvider';
import { RelationsSection } from './RelationsSection';
import type { SectionProps } from './types';
import type { Catalog } from './objects/model';

const scroll = Element.prototype.scrollIntoView;
beforeEach(() => { Element.prototype.scrollIntoView = vi.fn(); });
afterEach(() => { Element.prototype.scrollIntoView = scroll; vi.clearAllMocks(); });
const relation = { id: 't1.serves', source_type: 'Restaurant', target_type: 'Customer',
  targets_per_source: { minimum: 0, maximum: 1 }, sources_per_target: { minimum: 0, maximum: null },
  valid_writers: ['rules'], writer_options: [{ writer: 'rules', engine_id: 'rules', plugin: 'behaviour' }], binding_kind: 'writer_rule' };
const catalog: Catalog = { types: [
  { id: 'Restaurant', parents: [], abstract: false }, { id: 'Customer', parents: [], abstract: false },
  { id: 'SpecialRestaurant', parents: ['Restaurant'], abstract: false },
], relations: [relation] };
const draft = { id: 'research', engines: { rules: { plugin: 'behaviour', config: { packages: [] } } }, entities: [
  { id: 'kitchen', type: 'Restaurant', facts: {} }, { id: 'customer', type: 'Customer', facts: {} }, { id: 'other', type: 'Customer', facts: {} },
] };
function setup(document: Record<string, unknown> = draft, source: Catalog = catalog, zh = false) {
  const studioApi = { request: vi.fn().mockResolvedValue(source) };
  const edit = vi.fn(), replaceDraft = vi.fn(), goTo = vi.fn();
  const props = { studioApi, project: { id: 'w', revision: 1 }, draft: document, edit, replaceDraft, goTo,
    L: (en: string, cn: string) => zh ? cn : en, zh, issues: [], onProject: vi.fn(), saveDraft: vi.fn(), reportValidity: vi.fn(),
  } as unknown as SectionProps;
  const view = render(<I18nProvider><RelationsSection {...props} /></I18nProvider>);
  return { edit, replaceDraft, goTo, props, view };
}
async function choose(source = 'kitchen', target = 'customer') {
  fireEvent.click(screen.getByRole('button', { name: 'Add relation' }));
  fireEvent.change(await screen.findByLabelText('relation type'), { target: { value: relation.id } });
  fireEvent.change(screen.getByLabelText('source instance'), { target: { value: source } });
  fireEvent.change(screen.getByLabelText('target instance'), { target: { value: target } });
}
const expectedRef = (id: string, type: string) => ({ $ref: { run_id: 'research', epoch: '0', id, generation: 0, type_id: type } });

it('offers an honest empty state for initial links and write permissions', () => {
  setup({});
  expect(screen.getByText(/No initial links yet/)).toBeVisible();
  expect(screen.getByText(/Initial links take effect when the scene is initialized/)).toBeVisible();
  expect(screen.getByText('Engine write permissions')).toBeVisible();
});

it('T1-013: authors both concrete endpoints through the real behaviour bootstrap contract and retains them on reopen', async () => {
  const existingPackage = { path: 'behaviours/existing.yaml' };
  const { edit, replaceDraft, view, props } = setup({ ...draft, engines: { rules: { plugin: 'behaviour', config: { packages: [existingPackage], consumes: ['existing.field'] } } } });
  await choose();
  fireEvent.click(screen.getByRole('button', { name: 'Add relation' }));
  expect(edit).not.toHaveBeenCalled();
  const next = replaceDraft.mock.calls[0][0];
  expect(next.engines).toEqual(props.draft.engines);
  expect(next.bindings.relations).toEqual([{ writer: 'rules', relation: 't1.serves', type: 'Restaurant', ids: 'kitchen' }]);
  expect(next.behaviours[0]).toEqual(existingPackage);
  expect(next.behaviours[1]).toEqual({ format: 'aeroagentsim.behaviour-package/v1', id: 'workbench-link-1', revision: 1,
    predicates: {}, chains: {}, bindings: [], budgets: { max_transitions_per_instance_per_ns: 64, max_instances: 1 },
    bootstrap_relations: { owner: 'rules', assertions: [{ id: 'workbench-link-1', relation: 't1.serves', source: expectedRef('kitchen', 'Restaurant'), target: expectedRef('customer', 'Customer') }] } });
  view.unmount();
  setup(next);
  expect(screen.getByText('kitchen → customer')).toBeVisible();
  expect(screen.getByText('Initial links').querySelector('.wb-tag')).toHaveTextContent('1');
});

it('T1-013: shows the exact missing writer and a one-click path to execution components', async () => {
  const { replaceDraft, goTo } = setup(draft, { ...catalog, relations: [{ ...relation, valid_writers: [], writer_options: [] }] });
  await choose();
  expect(screen.getByRole('button', { name: 'Add relation' })).toBeDisabled();
  expect(screen.getByText(/No configured engine writes t1.serves/)).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: 'Execution components' }));
  expect(goTo).toHaveBeenCalledWith('components');
  expect(replaceDraft).not.toHaveBeenCalled();
});

it('blocks a configured writer that cannot assert initial links rather than claiming a link was added', async () => {
  const source = { ...catalog, relations: [{ ...relation, valid_writers: ['observer'], writer_options: [{ writer: 'observer', engine_id: 'observer', plugin: 'predicate' }] }] };
  const { replaceDraft } = setup({ ...draft, engines: { observer: { plugin: 'predicate', config: {} } } }, source);
  await choose();
  expect(screen.getByText(/Writer observer.*has no supported initial-link setup/)).toBeVisible();
  expect(screen.getByRole('button', { name: 'Add relation' })).toBeDisabled();
  expect(replaceDraft).not.toHaveBeenCalled();
});

it('blocks endpoint types that contradict the contract', async () => {
  setup(); await choose('customer', 'other');
  expect(screen.getByText(/do not match the relation contract/)).toBeVisible();
  expect(screen.getByRole('button', { name: 'Add relation' })).toBeDisabled();
});

it('supports inherited endpoint types and escapes literal source glob characters', async () => {
  const { replaceDraft } = setup({ ...draft, entities: [{ id: 'kitchen[1]*?', type: 'SpecialRestaurant' }, ...draft.entities.slice(1)] });
  await choose('kitchen[1]*?'); fireEvent.click(screen.getByRole('button', { name: 'Add relation' }));
  const next = replaceDraft.mock.calls[0][0];
  expect(next.bindings.relations[0].ids).toBe('kitchen[[]1][*][?]');
  expect(next.behaviours[0].bootstrap_relations.assertions[0].source).toEqual(expectedRef('kitchen[1]*?', 'SpecialRestaurant'));
});

it('blocks duplicate links and contract cardinality violations', async () => {
  const doc = { ...draft, behaviours: [{ id: 'existing', bootstrap_relations: { assertions: [
    { id: 'already', relation: relation.id, source: expectedRef('kitchen', 'Restaurant'), target: expectedRef('customer', 'Customer') },
  ] } }] };
  const { replaceDraft } = setup(doc); await choose();
  expect(screen.getByText('This initial link already exists.')).toBeVisible();
  expect(screen.getByRole('button', { name: 'Add relation' })).toBeDisabled();
  fireEvent.change(screen.getByLabelText('target instance'), { target: { value: 'other' } });
  expect(screen.getByText(/exceeds the number of endpoints/)).toBeVisible();
  expect(screen.getByRole('button', { name: 'Add relation' })).toBeDisabled();
  expect(replaceDraft).not.toHaveBeenCalled();
});

it('shows exact link removal before confirmation and retains shared write permissions', () => {
  const pkg = { id: 'existing', predicates: {}, bootstrap_relations: { owner: 'rules', assertions: [{ id: 'link', relation: relation.id, source: expectedRef('kitchen', 'Restaurant'), target: expectedRef('customer', 'Customer') }] } };
  const bindings = { relations: [{ writer: 'rules', relation: relation.id, type: 'Restaurant', ids: 'kitchen' }] };
  const { replaceDraft } = setup({ ...draft, behaviours: [pkg], bindings });
  fireEvent.click(screen.getByRole('button', { name: 'Remove' }));
  expect(screen.getByText(/Remove initial link link: kitchen → customer/)).toBeVisible();
  expect(replaceDraft).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: 'Confirm removal' }));
  const next = replaceDraft.mock.calls[0][0];
  expect(next.bindings).toEqual(bindings);
  expect(next.behaviours[0]).toEqual({ ...pkg, bootstrap_relations: { owner: 'rules', assertions: [] } });
});

it('renders only the active locale for link authoring and missing-writer guidance', async () => {
  setup(draft, { ...catalog, relations: [{ ...relation, valid_writers: [], writer_options: [] }] }, true);
  fireEvent.click(screen.getByRole('button', { name: '添加关系' }));
  fireEvent.change(await screen.findByLabelText('关系类型'), { target: { value: relation.id } });
  expect(screen.getByText(/没有已配置引擎写入 t1.serves/)).toBeVisible();
  expect(screen.queryByText(/No configured engine/)).toBeNull();
  expect(screen.queryByText(/validation only|source-id scope/)).toBeNull();
  expect(within(screen.getByLabelText('写入引擎')).getByText('没有可用写入者')).toBeVisible();
});
