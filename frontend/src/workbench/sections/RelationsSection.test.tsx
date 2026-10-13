import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { I18nProvider } from '../../i18n/I18nProvider';
import { RelationsSection } from './RelationsSection';
import type { SectionProps } from './types';

const scroll = Element.prototype.scrollIntoView;
beforeEach(() => { Element.prototype.scrollIntoView = vi.fn(); });
afterEach(() => { Element.prototype.scrollIntoView = scroll; vi.clearAllMocks(); });

const catalog = { types: [{ id: 'oo:UAV', parents: [], abstract: false }], relations: [
  { id: 'oo:relation:observation-subject', source_type: 'aas:KinematicObservation', target_type: 'oo:UAV',
    targets_per_source: { minimum: 1, maximum: 1 }, sources_per_target: { minimum: 0, maximum: null } },
] };

function setup(draft: Record<string, unknown>) {
  const studioApi = { request: vi.fn().mockResolvedValue(catalog) };
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
  fireEvent.change(screen.getByLabelText('target instance'), { target: { value: 'uav-1' } });
  fireEvent.click(screen.getByRole('button', { name: 'Add relation' }));
  await waitFor(() => expect(edit).toHaveBeenCalled());
  const [path, value] = (edit as ReturnType<typeof vi.fn>).mock.calls[0];
  expect(path).toBe('/bindings/relations');
  expect(value[0]).toEqual({ writer: 'operations', relation: 'oo:relation:observation-subject', type: 'aas:KinematicObservation', ids: 'obs-1,uav-1' });
});

it('blocks endpoints whose type contradicts the relation contract', async () => {
  setup({ entities: [
    { id: 'uav-1', type: 'oo:UAV', facts: {} },
    { id: 'uav-2', type: 'oo:UAV', facts: {} },
  ] });
  fireEvent.click(screen.getByRole('button', { name: 'Add relation' }));
  fireEvent.change(await screen.findByLabelText('relation type'), { target: { value: 'oo:relation:observation-subject' } });
  fireEvent.change(screen.getByLabelText('source instance'), { target: { value: 'uav-1' } });
  fireEvent.change(screen.getByLabelText('target instance'), { target: { value: 'uav-2' } });
  expect(screen.getByText(/do not match the relation contract/)).toBeTruthy();
  expect(screen.getByRole('button', { name: 'Add relation' }).hasAttribute('disabled')).toBe(true);
});
