import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { I18nProvider } from '../../i18n/I18nProvider';
import { RelationsSection } from './RelationsSection';
import type { SectionProps } from './types';
import type { Catalog } from './objects/model';
import { getOperationsState, operation, previewOperations } from './operations';
import type { Project, WorkbenchApi } from '../api';

const scroll = Element.prototype.scrollIntoView;
beforeEach(() => { Element.prototype.scrollIntoView = vi.fn(); sessionStorage.clear(); });
afterEach(() => { cleanup(); Element.prototype.scrollIntoView = scroll; vi.clearAllMocks(); });
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
function setup(document: Record<string, unknown> = draft, source: Catalog = catalog, zh = false, overrides: Partial<SectionProps> = {}) {
  const studioApi = { request: vi.fn().mockResolvedValue(source) };
  const edit = vi.fn(), replaceDraft = vi.fn(), goTo = vi.fn();
  const server = { id: 'w', revision: 2, scenario: document } as Project;
  const api = { request: vi.fn().mockResolvedValue({ id: 'w', revision: 2 }), project: vi.fn().mockResolvedValue(server) };
  const props = { api, studioApi, project: { id: 'w', revision: 1 }, draft: document, edit, replaceDraft, goTo,
    L: (en: string, cn: string) => zh ? cn : en, zh, issues: [], onProject: vi.fn(), saveDraft: vi.fn().mockResolvedValue({ id: 'w', revision: 1 }), reportValidity: vi.fn(), ...overrides,
  } as unknown as SectionProps;
  const view = render(<I18nProvider><RelationsSection {...props} /></I18nProvider>);
  return { api, edit, replaceDraft, goTo, props, view };
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

it('T1-013 / T1-R4-002: submits concrete endpoints to the server and adopts only the authoritative read', async () => {
  const existingPackage = { path: 'behaviours/existing.yaml' };
  const { api, edit, replaceDraft, props } = setup({ ...draft, engines: { rules: { plugin: 'behaviour', config: { packages: [existingPackage], consumes: ['existing.field'] } } } });
  await choose();
  fireEvent.click(screen.getByRole('button', { name: 'Add relation' }));
  expect(edit).not.toHaveBeenCalled();
  await waitFor(() => expect(props.onProject).toHaveBeenCalled());
  expect(replaceDraft).not.toHaveBeenCalled();
  expect(api.request).toHaveBeenCalledWith('/v1/studio/workspaces/w/relations', { body: { relation: relation.id, writer: 'rules', source: 'kitchen', target: 'customer', revision: 1 } });
  expect(api.project).toHaveBeenCalledWith('w');
  expect(props.onProject).toHaveBeenLastCalledWith(await api.project.mock.results[0].value);
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

it('passes inherited endpoint identities verbatim, leaving permission matching to the real registry', async () => {
  const { api, props } = setup({ ...draft, entities: [{ id: 'kitchen[1]*?', type: 'SpecialRestaurant' }, ...draft.entities.slice(1)] });
  await choose('kitchen[1]*?'); fireEvent.click(screen.getByRole('button', { name: 'Add relation' }));
  await waitFor(() => expect(props.onProject).toHaveBeenCalled());
  expect(api.request.mock.calls[0][1].body.source).toBe('kitchen[1]*?');
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

it('restores unconfirmed relation/writer/endpoints after Conversation or refresh with an unload warning (T4-R4-001)', async () => {
  const first = setup(); await choose();
  fireEvent.change(screen.getByLabelText('writer engine'), { target: { value: 'rules' } });
  first.view.unmount();
  const unload = new Event('beforeunload', { cancelable: true }); window.dispatchEvent(unload);
  expect(unload.defaultPrevented).toBe(true);
  const second = setup();
  fireEvent.click(screen.getByRole('button', { name: 'Add relation' }));
  await screen.findByLabelText('source instance');
  expect(screen.getByText('Restored unsaved inputs')).toBeVisible();
  expect(screen.getByLabelText('relation type')).toHaveValue(relation.id);
  expect(screen.getByLabelText('writer engine')).toHaveValue('rules');
  expect(screen.getByLabelText('source instance')).toHaveValue('kitchen');
  expect(screen.getByLabelText('target instance')).toHaveValue('customer');
  expect(second.api.request).not.toHaveBeenCalled();
});

it('holds relation addition and saving/refresh under the workspace lock (T1-R4-003 / T4-R4-004)', async () => {
  let saved!: (value: Project) => void, read!: (value: Project) => void;
  const saveDraft = vi.fn(() => { expect(getOperationsState('w').applying).toBe(true); return new Promise<Project>(resolve => { saved = resolve; }); });
  const api = {
    request: vi.fn(async () => { expect(getOperationsState('w').applying).toBe(true); return { id: 'w', revision: 3 }; }),
    project: vi.fn(() => { expect(getOperationsState('w').applying).toBe(true); return new Promise<Project>(resolve => { read = resolve; }); }),
  };
  const { props } = setup(draft, catalog, false, { api: api as unknown as WorkbenchApi, saveDraft });
  await choose(); fireEvent.click(screen.getByRole('button', { name: 'Add relation' }));
  expect(() => previewOperations(api as unknown as WorkbenchApi, { id: 'w', revision: 1 } as Project, [operation('set_duration', { seconds: 600 })])).toThrow(/Applying/);
  await act(async () => saved({ id: 'w', revision: 2 } as Project));
  await waitFor(() => expect(api.project).toHaveBeenCalledOnce());
  expect(api.request).toHaveBeenCalledWith('/v1/studio/workspaces/w/relations', { body: { relation: relation.id, writer: 'rules', source: 'kitchen', target: 'customer', revision: 2 } });
  expect(getOperationsState('w').applying).toBe(true);
  expect(props.onProject).not.toHaveBeenCalled();
  const authoritative = { id: 'w', revision: 3, scenario: draft, latest_version: { version: 1 } } as unknown as Project;
  await act(async () => read(authoritative));
  expect(props.onProject).toHaveBeenCalledWith(authoritative);
  expect(getOperationsState('w').applying).toBe(false);
  expect(Object.keys(sessionStorage).filter(key => key.startsWith('aas.staged.'))).toHaveLength(0);
});

it('keeps staged link inputs and the actual failure when the server rejects a non-covering permission', async () => {
  const fixture = setup();
  fixture.api.request.mockRejectedValue(Error('Edit the existing source scope / 请修改现有源范围'));
  await choose(); fireEvent.click(screen.getByRole('button', { name: 'Add relation' }));
  expect(await screen.findByRole('alert')).toHaveTextContent('Edit the existing source scope');
  expect(screen.getByLabelText('source instance')).toHaveValue('kitchen');
  expect(fixture.props.onProject).not.toHaveBeenCalled();
  expect(fixture.api.project).not.toHaveBeenCalled();
  expect(getOperationsState('w').applying).toBe(false);
});
