import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { I18nProvider } from '../../../i18n/I18nProvider';
import { AddObjects } from './AddObjects';

const scroll = Element.prototype.scrollIntoView;
beforeEach(() => { Element.prototype.scrollIntoView = vi.fn(); });
afterEach(() => { Element.prototype.scrollIntoView = scroll; });

const L = (en: string, _zh?: string) => en;
const catalog = { types: [
  { id: 'oo:UAV', parents: ['oo:Aircraft'], abstract: false },
  { id: 'oo:Aircraft', parents: [], abstract: true },
  { id: 'oo:UAV2', parents: [], abstract: false },
], relations: [] };

function setup(apiOverrides: Record<string, unknown> = {}, entities: Array<Record<string, unknown>> = []) {
  const api = {
    save: vi.fn().mockResolvedValue({ id: 'w', revision: 2, scenario: { entities } }),
    request: vi.fn().mockResolvedValue({ id: 'w', revision: 3, scenario: { entities } }),
    ...apiOverrides,
  };
  const studioApi = {
    request: vi.fn().mockImplementation(async (path: string) => {
      if (path.startsWith('/v1/studio/types?')) return catalog;
      if (path.includes('/types/oo%3A') || path.includes('/types/oo:')) return { id: 'oo:UAV', parents: [], abstract: false, fields: [
        { id: 'aas.studio.velocity_enu_m_s', declaring_type: 'oo:ModelObject', schema: { type: 'vector', length: 3 }, metadata: { unit: 'm/s' } },
        { id: 'aas.studio.energy_j', declaring_type: 'oo:ModelObject', schema: { type: 'number' }, metadata: { unit: 'J' } },
      ] } as never;
      return {} as never;
    }),
  };
  const onProject = vi.fn();
  render(<I18nProvider><AddObjects api={api as never} studioApi={studioApi as never} project={{ id: 'w', revision: 1 }}
    draft={{ entities } as never} onProject={onProject} L={L} onClose={() => undefined} /></I18nProvider>);
  return { api, onProject, studioApi };
}

it('shows abstract types as disabled with a reason and cannot create them', async () => {
  setup();
  expect(await screen.findByRole('option', { name: /oo:Aircraft/ })).toBeDisabled();
  expect(screen.getByText(/abstract — cannot be instantiated/)).toBeTruthy();
});

it('rejects ids outside the authoring identifier pattern', async () => {
  setup();
  await screen.findByRole('option', { name: /^oo:UAV/ });
  fireEvent.click(screen.getByRole('option', { name: /^oo:UAV/ }));
  fireEvent.change(screen.getByLabelText('id (or id prefix)'), { target: { value: 'bad id' } });
  expect(screen.getByRole('button', { name: /Create 1 objects/ })).toBeDisabled();
});

it('flags a duplicate id instead of silently sending a second placement', async () => {
  setup({}, [{ id: 'uav-1', type: 'oo:UAV', facts: {} }]);
  await screen.findByRole('option', { name: /^oo:UAV/ });
  fireEvent.click(screen.getByRole('option', { name: /^oo:UAV/ }));
  fireEvent.change(screen.getByLabelText('id (or id prefix)'), { target: { value: 'uav-1' } });
  expect(screen.getByText(/an instance already uses uav-1/)).toBeTruthy();
  expect(screen.getByRole('button', { name: /Create 1 objects/ })).toBeDisabled();
});

it('places each reviewed instance and adopts the updated workspace', async () => {
  const { api, onProject } = setup();
  await screen.findByRole('option', { name: /^oo:UAV/ });
  fireEvent.click(screen.getByRole('option', { name: /^oo:UAV/ }));
  fireEvent.change(screen.getByLabelText('id (or id prefix)'), { target: { value: 'uav' } });
  fireEvent.change(screen.getByLabelText('count'), { target: { value: '2' } });
  fireEvent.change(screen.getByLabelText('east (m)'), { target: { value: '10' } });
  fireEvent.change(screen.getByLabelText('spacing along east (m)'), { target: { value: '5' } });
  await waitFor(() => expect(screen.getByText('uav-2')).toBeTruthy());
  fireEvent.click(screen.getByRole('button', { name: /Create 2 objects/ }));
  await waitFor(() => expect(api.request).toHaveBeenCalledTimes(2));
  const first = JSON.stringify((api.request as ReturnType<typeof vi.fn>).mock.calls[0][1].body);
  expect(first).toContain('"id":"uav-1"');
  expect(JSON.stringify((api.request as ReturnType<typeof vi.fn>).mock.calls[1][1].body)).toContain('[10,0,0]');
  expect(onProject).toHaveBeenCalled();
});

it('reports which instances failed and still lists the created ones', async () => {
  const { api } = setup({ request: vi.fn().mockRejectedValueOnce(Object.assign(Error('HTTP 422: bad'), { status: 422 })).mockResolvedValue({ id: 'w', revision: 3, scenario: {} }) });
  await screen.findByRole('option', { name: /^oo:UAV/ });
  fireEvent.click(screen.getByRole('option', { name: /^oo:UAV/ }));
  fireEvent.change(screen.getByLabelText('id (or id prefix)'), { target: { value: 'uav' } });
  fireEvent.change(screen.getByLabelText('count'), { target: { value: '2' } });
  await waitFor(() => expect(screen.getByText('uav-2')).toBeTruthy());
  fireEvent.click(screen.getByRole('button', { name: /Create 2 objects/ }));
  expect(await screen.findByText(/Created: uav-2/)).toBeTruthy();
  expect(screen.getByText(/Failed on uav-1/)).toBeTruthy();
});
