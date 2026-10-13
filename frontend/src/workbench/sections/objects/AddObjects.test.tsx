import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { I18nProvider } from '../../../i18n/I18nProvider';
import { AddObjects } from './AddObjects';
import { stringifyLossless } from '../../../feeds/lossless-json';
import type { ComponentProps } from 'react';

const scroll = Element.prototype.scrollIntoView;
beforeEach(() => { Element.prototype.scrollIntoView = vi.fn(); });
afterEach(() => { Element.prototype.scrollIntoView = scroll; });

const L = (en: string, _zh?: string) => en;
const catalog = { types: [
  { id: 'oo:UAV', parents: ['oo:Aircraft'], abstract: false },
  { id: 'oo:Aircraft', parents: [], abstract: true },
  { id: 'oo:UAV2', parents: [], abstract: false },
], relations: [] };

function setup(apiOverrides: Record<string, unknown> = {}, entities: Array<Record<string, unknown>> = [], source = catalog, details?: Record<string, unknown>, props: Partial<ComponentProps<typeof AddObjects>> = {}) {
  const api = {
    save: vi.fn().mockResolvedValue({ id: 'w', revision: 2, scenario: { entities } }),
    request: vi.fn().mockResolvedValue({ id: 'w', revision: 3, scenario: { entities } }),
    ...apiOverrides,
  };
  const studioApi = {
    request: vi.fn().mockImplementation(async (path: string) => {
      if (path.startsWith('/v1/studio/types?')) return source;
      if (details) return details;
      if (path.includes('/types/oo%3A') || path.includes('/types/oo:')) return { id: 'oo:UAV', parents: [], abstract: false, fields: [
        { id: 'aas.studio.velocity_enu_m_s', declaring_type: 'oo:ModelObject', schema: { type: 'vector', length: 3 }, metadata: { unit: 'm/s' } },
        { id: 'aas.studio.energy_j', declaring_type: 'oo:ModelObject', schema: { type: 'number' }, metadata: { unit: 'J' } },
      ] } as never;
      return {} as never;
    }),
  };
  const onProject = vi.fn();
  render(<I18nProvider><AddObjects api={api as never} studioApi={studioApi as never} project={{ id: 'w', revision: 1 }}
    draft={{ entities } as never} onProject={onProject} L={L} onClose={() => undefined} {...props} /></I18nProvider>);
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

it('reports which instances failed and stops rather than chaining stale placements', async () => {
  const { api } = setup({ request: vi.fn().mockRejectedValueOnce(Object.assign(Error('HTTP 422: bad'), { status: 422 })).mockResolvedValue({ id: 'w', revision: 3, scenario: {} }) });
  await chooseUav();
  fireEvent.change(screen.getByLabelText('id (or id prefix)'), { target: { value: 'uav' } });
  fireEvent.change(screen.getByLabelText('count'), { target: { value: '2' } });
  await waitFor(() => expect(screen.getByText('uav-2')).toBeTruthy());
  fireEvent.click(screen.getByRole('button', { name: /Create 2 objects/ }));
  expect(await screen.findByText(/Failed on uav-1/)).toBeTruthy();
  // The batch stops at the first failure; the second placement is not replayed against a stale draft.
  expect(api.request).toHaveBeenCalledTimes(1);
});

it('sends explicit integral energy as a JSON number without tagging or converting it to text (T1-R2-001)', async () => {
  const { api } = setup();
  await chooseUav();
  fireEvent.change(screen.getByLabelText('id (or id prefix)'), { target: { value: 'point' } });
  fireEvent.change(screen.getByLabelText('aas.studio.energy_j'), { target: { value: '100000' } });
  fireEvent.click(screen.getByRole('button', { name: 'Create 1 objects' }));
  await screen.findByText('Created: point');
  const body = api.request.mock.calls[0][1].body;
  expect(body.facts['aas.studio.energy_j']).toBe(100000);
  expect(typeof body.facts['aas.studio.energy_j']).toBe('number');
  expect(JSON.parse(stringifyLossless(body)).facts['aas.studio.energy_j']).toBe(100000);
});

it('blocks malformed numeric facts without placing or substituting a default', async () => {
  const { api } = setup();
  await chooseUav();
  fireEvent.change(screen.getByLabelText('id (or id prefix)'), { target: { value: 'point' } });
  fireEvent.change(screen.getByLabelText('aas.studio.energy_j'), { target: { value: '100000oops' } });
  expect(screen.getByLabelText('aas.studio.energy_j')).toHaveAttribute('aria-invalid', 'true');
  expect(screen.getByRole('button', { name: /Create .* objects/ })).toBeDisabled();
  expect(api.save).not.toHaveBeenCalled();
  expect(api.request).not.toHaveBeenCalled();
});

const regionField = { id: 'aas.studio.airspace', declaring_type: 'aas:StudioAirspace', metadata: { frame: 'enu', unit: 'm' },
  schema: { type: 'record', members: { polygon: { type: 'array', items: { type: 'vector', length: 2, items: { type: 'number' } } },
    floor_m: { type: 'number' }, ceiling_m: { type: 'number' } }, required: ['polygon', 'floor_m', 'ceiling_m'] } };
function airspace(fields: unknown[] = [regionField]) {
  return setup({}, [], { types: [{ id: 'aas:StudioAirspace', parents: [], abstract: false }], relations: [] },
    { id: 'aas:StudioAirspace', parents: [], abstract: false, fields });
}

it('uses the catalogue region record to author an airspace with workflow, polygon and altitude limits (T1-018)', async () => {
  const { api } = airspace();
  fireEvent.click(await screen.findByRole('option', { name: 'aas:StudioAirspace' }));
  const polygon = await screen.findByLabelText(/polygon \(east,north/);
  expect(screen.getByLabelText('Kind')).toHaveValue('airspace');
  expect(screen.getByLabelText('Engine')).toHaveValue('workflow');
  expect(screen.getByLabelText('Kind')).toBeDisabled();
  expect(screen.getByLabelText('Engine')).toBeDisabled();
  expect(screen.queryByLabelText('east (m)')).toBeNull();
  fireEvent.change(screen.getByLabelText('id (or id prefix)'), { target: { value: 'zone' } });
  fireEvent.change(polygon, { target: { value: '0,0; 40,0; 40,30' } });
  fireEvent.change(screen.getByLabelText('Floor (m)'), { target: { value: '10' } });
  fireEvent.change(screen.getByLabelText('Ceiling (m)'), { target: { value: '60' } });
  fireEvent.click(screen.getByRole('button', { name: 'Create 1 objects' }));
  await screen.findByText('Created: zone');
  expect(api.request.mock.calls[0][1].body).toEqual({ id: 'zone', type: 'aas:StudioAirspace', kind: 'airspace', engine: 'workflow',
    position: [0, 0, 0], polygon: [[0, 0], [40, 0], [40, 30]], floor_m: 10, ceiling_m: 60 });
});

it('rejects missing polygon coordinates and reversed altitude limits before creating an airspace (T1-018)', async () => {
  const { api } = airspace();
  fireEvent.click(await screen.findByRole('option', { name: 'aas:StudioAirspace' }));
  const polygon = await screen.findByLabelText(/polygon \(east,north/);
  fireEvent.change(screen.getByLabelText('id (or id prefix)'), { target: { value: 'zone' } });
  fireEvent.change(polygon, { target: { value: '0,0; 40,; 40,30' } });
  expect(screen.getByRole('button', { name: 'Create 1 objects' })).toBeDisabled();
  fireEvent.change(polygon, { target: { value: '0,0; 40,0; 40,30' } });
  fireEvent.change(screen.getByLabelText('Floor (m)'), { target: { value: '120' } });
  expect(screen.getByRole('button', { name: 'Create 1 objects' })).toBeDisabled();
  expect(api.save).not.toHaveBeenCalled();
  expect(api.request).not.toHaveBeenCalled();
});

it('disables an airspace contract when its declared geometry is not supported instead of creating a moving point (T1-018)', async () => {
  const { api } = airspace([]);
  const option = await screen.findByRole('option', { name: 'aas:StudioAirspace' });
  fireEvent.click(option);
  expect(await screen.findByText(/needs a region editor/)).toBeTruthy();
  expect(option).toBeDisabled();
  fireEvent.change(screen.getByLabelText('id (or id prefix)'), { target: { value: 'zone' } });
  expect(screen.getByRole('button', { name: 'Create 1 objects' })).toBeDisabled();
  expect(api.request).not.toHaveBeenCalled();
});

it('honours an explicit kind and engine in the contract catalogue', async () => {
  setup({}, [], { types: [{ id: 'custom:Place', parents: [], abstract: false, kind: 'facility', engine: 'workflow' }], relations: [] } as never,
    { id: 'custom:Place', parents: [], abstract: false, fields: [] });
  fireEvent.click(await screen.findByRole('option', { name: 'custom:Place' }));
  await screen.findByText('This type declares no extra contract fields.');
  expect(screen.getByLabelText('Kind')).toHaveValue('facility');
  expect(screen.getByLabelText('Engine')).toHaveValue('workflow');
  expect(screen.getByLabelText('Kind')).toBeDisabled();
});

it('uses the shared draft save before placement so local edits can adopt the authoritative project (T1-007)', async () => {
  const saveDraft = vi.fn().mockResolvedValue({ id: 'w', revision: 2, scenario: { entities: [] } });
  const { api, onProject } = setup({}, [], catalog, undefined, { saveDraft });
  await chooseUav();
  fireEvent.change(screen.getByLabelText('id (or id prefix)'), { target: { value: 'point' } });
  fireEvent.click(screen.getByRole('button', { name: 'Create 1 objects' }));
  await screen.findByText('Created: point');
  expect(saveDraft).toHaveBeenCalledTimes(1);
  expect(api.save).not.toHaveBeenCalled();
  expect(saveDraft.mock.invocationCallOrder[0]).toBeLessThan(api.request.mock.invocationCallOrder[0]);
  expect(onProject).toHaveBeenLastCalledWith(expect.objectContaining({ revision: 3 }));
});
