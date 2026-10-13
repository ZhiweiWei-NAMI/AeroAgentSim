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

async function chooseUav() {
  fireEvent.click(await screen.findByRole('option', { name: /^oo:UAV(?:\s|$)/ }));
  await screen.findByText('aas.studio.velocity_enu_m_s');
}

it('shows abstract types as disabled with a reason and cannot create them', async () => {
  const { api } = setup();
  const option = await screen.findByRole('option', { name: /^oo:Aircraft(?:\s|$)/ });
  expect(option).toBeDisabled();
  expect(screen.getByText(/abstract — cannot be instantiated/)).toBeTruthy();
  fireEvent.click(option);
  expect(screen.queryByLabelText('id (or id prefix)')).toBeNull();
  expect(api.request).not.toHaveBeenCalled();
});

it('rejects ids outside the authoring identifier pattern', async () => {
  const { api } = setup();
  await chooseUav();
  fireEvent.change(screen.getByLabelText('id (or id prefix)'), { target: { value: 'bad id' } });
  const create = screen.getByRole('button', { name: 'Create 0 objects' });
  expect(create).toBeDisabled();
  fireEvent.click(create);
  expect(api.save).not.toHaveBeenCalled();
  expect(api.request).not.toHaveBeenCalled();
});

it('flags a duplicate id instead of silently sending a second placement', async () => {
  const { api } = setup({}, [{ id: 'uav-1', type: 'oo:UAV', facts: {} }]);
  await chooseUav();
  fireEvent.change(screen.getByLabelText('id (or id prefix)'), { target: { value: 'uav-1' } });
  expect(screen.getByText(/an instance already uses uav-1/)).toBeTruthy();
  const create = screen.getByRole('button', { name: 'Create 0 objects' });
  expect(create).toBeDisabled();
  fireEvent.click(create);
  expect(api.save).not.toHaveBeenCalled();
  expect(api.request).not.toHaveBeenCalled();
});

it('places each reviewed instance and adopts the updated workspace', async () => {
  const firstPlaced = { id: 'w', revision: 3, scenario: { entities: [{ id: 'uav-1' }] } };
  const allPlaced = { id: 'w', revision: 4, scenario: { entities: [{ id: 'uav-1' }, { id: 'uav-2' }] } };
  const request = vi.fn().mockResolvedValueOnce(firstPlaced).mockResolvedValueOnce(allPlaced);
  const { api, onProject } = setup({ request });
  await chooseUav();
  fireEvent.change(screen.getByLabelText('id (or id prefix)'), { target: { value: 'uav' } });
  fireEvent.change(screen.getByLabelText('count'), { target: { value: '2' } });
  fireEvent.change(screen.getByLabelText('east (m)'), { target: { value: '10' } });
  fireEvent.change(screen.getByLabelText('spacing along east (m)'), { target: { value: '5' } });
  await waitFor(() => expect(screen.getByText('uav-2')).toBeTruthy());
  fireEvent.click(screen.getByRole('button', { name: /Create 2 objects/ }));
  expect(await screen.findByText('Created: uav-1, uav-2')).toBeTruthy();
  expect(api.save).toHaveBeenCalledWith('w', { entities: [] }, 1);
  expect(api.save.mock.invocationCallOrder[0]).toBeLessThan(request.mock.invocationCallOrder[0]);
  expect(request.mock.calls).toEqual([
    ['/v1/studio/workspaces/w/place', { body: { id: 'uav-1', type: 'oo:UAV', kind: 'entity', engine: 'kinematic', position: [10, 0, 0] } }],
    ['/v1/studio/workspaces/w/place', { body: { id: 'uav-2', type: 'oo:UAV', kind: 'entity', engine: 'kinematic', position: [15, 0, 0] } }],
  ]);
  expect(onProject).toHaveBeenCalledWith(firstPlaced);
  expect(onProject).toHaveBeenLastCalledWith(allPlaced);
});

it('reports which instances failed and still lists the created ones', async () => {
  const { api } = setup({ request: vi.fn().mockRejectedValueOnce(Object.assign(Error('HTTP 422: bad'), { status: 422 })).mockResolvedValue({ id: 'w', revision: 3, scenario: {} }) });
  await chooseUav();
  fireEvent.change(screen.getByLabelText('id (or id prefix)'), { target: { value: 'uav' } });
  fireEvent.change(screen.getByLabelText('count'), { target: { value: '2' } });
  await waitFor(() => expect(screen.getByText('uav-2')).toBeTruthy());
  fireEvent.click(screen.getByRole('button', { name: /Create 2 objects/ }));
  expect(await screen.findByText(/Created: uav-2/)).toBeTruthy();
  expect(screen.getByText(/Failed on uav-1/)).toBeTruthy();
  expect(api.request).toHaveBeenCalledTimes(2);
});
