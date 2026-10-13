import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { I18nProvider } from '../../i18n/I18nProvider';
import { RelationsSection } from './RelationsSection';
import type { SectionProps } from './types';
import type { Catalog } from './objects/model';

const scroll = Element.prototype.scrollIntoView;
beforeEach(() => { Element.prototype.scrollIntoView = vi.fn(); });
afterEach(() => { Element.prototype.scrollIntoView = scroll; vi.clearAllMocks(); });

const catalog = { types: [{ id: 'oo:UAV', parents: [], abstract: false }], relations: [
  { id: 'oo:relation:observation-subject', source_type: 'aas:KinematicObservation', target_type: 'oo:UAV',
    targets_per_source: { minimum: 1, maximum: 1 }, sources_per_target: { minimum: 0, maximum: null }, valid_writers: ['behaviour'], writer_options: [{ writer: 'behaviour', engine_id: 'rules', plugin: 'behaviour' }], binding_kind: 'writer_rule' },
] };

function setup(draft: Record<string, unknown>, source: Catalog = catalog) {
  const studioApi = { request: vi.fn().mockResolvedValue(source) };
  const edit = vi.fn();
  const props = { studioApi, project: { id: 'w', revision: 1 } , draft, edit,
    replaceDraft: vi.fn(), focus: undefined, L: (en: string, _zh?: string) => en, issues: [], onProject: vi.fn(), saveDraft: vi.fn(), reportValidity: vi.fn(),
  } as unknown as SectionProps;
  render(<I18nProvider><RelationsSection {...props} /></I18nProvider>);
  return { edit, studioApi };
}

it('offers an actionable empty state on a blank project', () => {
  setup({});
  expect(screen.getByText(/Add one above/)).toBeTruthy();
  expect(screen.getByRole('button', { name: 'Add relation' })).toBeTruthy();
});

it('adds an edge in the loader shape {writer, relation, type, ids}', async () => {
  const { edit } = setup({ entities: [
    { id: 'obs-1', type: 'aas:KinematicObservation', facts: {} },
    { id: 'uav-1', type: 'oo:UAV', facts: {} },
  ] });
  fireEvent.click(screen.getByRole('button', { name: 'Add relation' }));
  fireEvent.change(await screen.findByLabelText('relation type'), { target: { value: 'oo:relation:observation-subject' } });
  fireEvent.change(screen.getByLabelText('source instance'), { target: { value: 'obs-1' } });
  fireEvent.change(screen.getByLabelText(/target instance/), { target: { value: 'uav-1' } });
  fireEvent.click(screen.getByRole('button', { name: 'Add relation' }));
  await waitFor(() => expect(edit).toHaveBeenCalled());
  const [path, value] = (edit as ReturnType<typeof vi.fn>).mock.calls[0];
  expect(path).toBe('/bindings/relations');
  // `ids` is a source-id glob scope (fnmatchcase in the loader), not an endpoint list.
  expect(value[0]).toEqual({ writer: 'behaviour', relation: 'oo:relation:observation-subject', type: 'aas:KinematicObservation', ids: 'obs-1' });
});

it('blocks endpoints whose type contradicts the relation contract', async () => {
  setup({ entities: [
    { id: 'uav-1', type: 'oo:UAV', facts: {} },
    { id: 'uav-2', type: 'oo:UAV', facts: {} },
  ] });
  fireEvent.click(screen.getByRole('button', { name: 'Add relation' }));
  fireEvent.change(await screen.findByLabelText('relation type'), { target: { value: 'oo:relation:observation-subject' } });
  fireEvent.change(screen.getByLabelText('source instance'), { target: { value: 'uav-1' } });
  fireEvent.change(screen.getByLabelText(/target instance/), { target: { value: 'uav-2' } });
  expect(screen.getByText(/do not match the relation contract/)).toBeTruthy();
  expect(screen.getByRole('button', { name: 'Add relation' }).hasAttribute('disabled')).toBe(true);
});

it('requires an actual catalog writer instead of inventing an operations writer', async () => {
  const source = { ...catalog, relations: [{ ...catalog.relations[0], valid_writers: [], writer_options: [] }] };
  const { edit } = setup({ entities: [{ id: 'obs', type: 'aas:KinematicObservation' }, { id: 'uav', type: 'oo:UAV' }] }, source);
  fireEvent.click(screen.getByRole('button', { name: 'Add relation' }));
  fireEvent.change(await screen.findByLabelText('relation type'), { target: { value: catalog.relations[0].id } });
  fireEvent.change(screen.getByLabelText('source instance'), { target: { value: 'obs' } });
  fireEvent.change(screen.getByLabelText(/target instance/), { target: { value: 'uav' } });
  expect(screen.getByRole('button', { name: 'Add relation' })).toBeDisabled();
  expect(screen.getByText(/No configured engine writes this relation/)).toBeVisible();
  expect(edit).not.toHaveBeenCalled();
});

it('accepts inherited endpoint types and the selected writer partition', async () => {
  const source = { ...catalog, types: [...catalog.types, { id: 'special:Observation', parents: ['aas:KinematicObservation'], abstract: false }],
    relations: [{ ...catalog.relations[0], valid_writers: ['behaviour', 'observer'], writer_options: [{ writer: 'observer', engine_id: 'observe', plugin: 'predicate' }] }] };
  const { edit } = setup({ entities: [{ id: 'obs', type: 'special:Observation' }, { id: 'uav', type: 'oo:UAV' }] }, source);
  fireEvent.click(screen.getByRole('button', { name: 'Add relation' }));
  fireEvent.change(await screen.findByLabelText('relation type'), { target: { value: catalog.relations[0].id } });
  fireEvent.change(screen.getByLabelText('writer engine'), { target: { value: 'observer' } });
  fireEvent.change(screen.getByLabelText('source instance'), { target: { value: 'obs' } });
  fireEvent.change(screen.getByLabelText(/target instance/), { target: { value: 'uav' } });
  fireEvent.click(screen.getByRole('button', { name: 'Add relation' }));
  expect(edit).toHaveBeenCalledWith('/bindings/relations', [{ writer: 'observer', relation: catalog.relations[0].id, type: 'aas:KinematicObservation', ids: 'obs' }]);
});
