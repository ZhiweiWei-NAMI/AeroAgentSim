import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { I18nProvider } from '../../../i18n/I18nProvider';
import { AddObjects } from './AddObjects';
import { stringifyLossless } from '../../../feeds/lossless-json';
import type { ComponentProps } from 'react';
import type { PlacePickerMapProps } from '../space/PlacePickerMap';
import { getOperationsState, operation, previewOperations } from '../operations';
import type { Project, WorkbenchApi } from '../../api';

vi.mock('../space/PlacePickerMap', async importOriginal => ({ ...await importOriginal<object>(), PlacePickerMap: ({ position, onPick }: PlacePickerMapProps) => <div>
  <button onClick={() => onPick(80, -25, true)}>Map click</button>
  <output aria-label="Map marker">{JSON.stringify(position)}</output>
</div> }));

const scroll = Element.prototype.scrollIntoView;
beforeEach(() => { Element.prototype.scrollIntoView = vi.fn(); sessionStorage.clear(); });
afterEach(() => { cleanup(); Element.prototype.scrollIntoView = scroll; });

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
    project: vi.fn().mockResolvedValue({ id: 'w', revision: 3, scenario: { entities } }),
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
  const view = render(<I18nProvider><AddObjects api={api as never} studioApi={studioApi as never} project={{ id: 'w', revision: 1 }}
    draft={{ entities } as never} onProject={onProject} L={L} onClose={() => undefined} {...props} /></I18nProvider>);
  return { api, onProject, studioApi, view };
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
  const { api, onProject } = setup({ request, project: vi.fn().mockResolvedValue(allPlaced) });
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
  expect(api.project).toHaveBeenCalledWith('w');
  expect(onProject).not.toHaveBeenCalledWith(firstPlaced);
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

it('uses the shared picker with live coordinates, clears invalid markers and preserves height', async () => {
  setup({}, [], catalog, undefined, { project_detail: { region: { extract: 'test', bounds: [121, 31, 122, 32], alt: 0, level_height_m: 3.5 }, scene: { origin: { lat: 31.5, lon: 121.5, alt: 0 } } } });
  await chooseUav();
  fireEvent.change(screen.getByLabelText('up (m)'), { target: { value: '10' } });
  fireEvent.click(screen.getByRole('button', { name: 'Pick on map' }));
  fireEvent.click(screen.getByRole('button', { name: 'Map click' }));
  expect(screen.getByLabelText('east (m)')).toHaveValue('80.0');
  expect(screen.getByLabelText('north (m)')).toHaveValue('-25.0');
  expect(screen.getByLabelText('up (m)')).toHaveValue('10');
  fireEvent.change(screen.getByLabelText('east (m)'), { target: { value: '90' } });
  expect(screen.getByLabelText('Map marker')).toHaveTextContent('[90,-25]');
  fireEvent.change(screen.getByLabelText('east (m)'), { target: { value: '' } });
  expect(screen.getByLabelText('Map marker')).toHaveTextContent('null');
  expect(screen.getByRole('button', { name: 'Map click' })).toBeVisible();
});

it('restores all unconfirmed advanced inputs and warns after Conversation/refresh (T1-R4-001)', async () => {
  const first = setup();
  await chooseUav();
  const values = { 'Search the workspace type catalog': 'UAV', 'Kind': 'facility', 'id (or id prefix)': 'research-static',
    'count': '2', 'east (m)': '12.', 'north (m)': '34', 'up (m)': '56', 'spacing along east (m)': '20', 'aas.studio.energy_j': '100000' };
  for (const [label, value] of Object.entries(values)) fireEvent.change(screen.getByLabelText(label), { target: { value } });
  expect(screen.getByLabelText('Engine')).toHaveValue('workflow');
  first.view.unmount();
  const unload = new Event('beforeunload', { cancelable: true }); window.dispatchEvent(unload);
  expect(unload.defaultPrevented).toBe(true);
  const second = setup();
  await screen.findByLabelText('aas.studio.energy_j');
  expect(screen.getByText('Restored unsaved inputs')).toBeVisible();
  for (const [label, value] of Object.entries(values)) expect(screen.getByLabelText(label)).toHaveValue(value);
  expect(screen.getByLabelText('Engine')).toHaveValue('workflow');
  expect(second.api.request).not.toHaveBeenCalled();
});

it('holds the lock from draft save through the full batch and authoritative read, preserving version and capabilities (T1-R4-003 / T1-R4-005)', async () => {
  let saved!: (value: Project) => void, read!: (value: Project) => void;
  const partial = { id: 'w', revision: 4, scenario: { entities: [{ id: 'batch-1' }, { id: 'batch-2' }] } };
  const full = { ...partial, latest_version: { version: 1 }, capabilities: { lowalt: true, region: false, network: false, city: false } } as unknown as Project;
  const save = vi.fn(() => { expect(getOperationsState('w').applying).toBe(true); return new Promise<Project>(resolve => { saved = resolve; }); });
  const request = vi.fn(async () => { expect(getOperationsState('w').applying).toBe(true); return partial; });
  const project = vi.fn(() => { expect(getOperationsState('w').applying).toBe(true); return new Promise<Project>(resolve => { read = resolve; }); });
  const fixture = setup({ save, request, project });
  await chooseUav();
  fireEvent.change(screen.getByLabelText('id (or id prefix)'), { target: { value: 'batch' } });
  fireEvent.change(screen.getByLabelText('count'), { target: { value: '2' } });
  fireEvent.click(screen.getByRole('button', { name: 'Create 2 objects' }));
  const sibling = { request: vi.fn() } as unknown as WorkbenchApi;
  expect(() => previewOperations(sibling, { id: 'w', revision: 1 } as Project, [operation('set_wind', { speed_mps: 3 })])).toThrow(/Applying/);
  await act(async () => saved({ id: 'w', revision: 2 } as Project));
  await waitFor(() => expect(project).toHaveBeenCalledOnce());
  expect(request).toHaveBeenCalledTimes(2);
  expect(getOperationsState('w').applying).toBe(true);
  expect(fixture.onProject).not.toHaveBeenCalled();
  await act(async () => read(full));
  expect(await screen.findByText('Created: batch-1, batch-2')).toBeVisible();
  expect(fixture.onProject).toHaveBeenCalledTimes(1);
  expect(fixture.onProject).toHaveBeenLastCalledWith(full);
  expect(getOperationsState('w').applying).toBe(false);
  expect(Object.keys(sessionStorage).filter(key => key.startsWith('aas.staged.'))).toHaveLength(0);
});

it('defaults to the first compatible engine and explains the excluded motion type (T1-R4-006)', async () => {
  const type = { id: 'aas:StudioFacility', parents: [], abstract: false };
  const { api } = setup({}, [], { types: [type], relations: [] }, { ...type, fields: [] },
    { draft: { entities: [], engines: { motion: { plugin: 'kinematic', config: { type_id: 'lowalt:UAV' } } } } });
  fireEvent.click(await screen.findByRole('option', { name: 'aas:StudioFacility' }));
  await screen.findByText('This type declares no extra contract fields.');
  expect(screen.getByLabelText('Engine')).toHaveValue('workflow');
  expect(screen.queryByRole('option', { name: /kinematic —/ })).toBeNull();
  expect(screen.getByText(/motion engine accepts lowalt:UAV/)).toBeVisible();
  fireEvent.change(screen.getByLabelText('id (or id prefix)'), { target: { value: 'compatible' } });
  fireEvent.click(screen.getByRole('button', { name: 'Create 1 objects' }));
  await screen.findByText('Created: compatible');
  expect(api.request.mock.calls[0][1].body.engine).toBe('workflow');
});

it('retains a restored incompatible engine visibly and blocks creation until the researcher changes it (T1-R4-006)', async () => {
  const first = setup(); await chooseUav();
  fireEvent.change(screen.getByLabelText('id (or id prefix)'), { target: { value: 'moving' } });
  first.view.unmount();
  const second = setup({}, [], catalog, undefined, { draft: { entities: [], engines: { motion: { plugin: 'kinematic', config: { type_id: 'another:type' } } } } });
  await screen.findByLabelText('aas.studio.energy_j');
  expect(screen.getByLabelText('Engine')).toHaveValue('kinematic');
  expect(screen.getByRole('button', { name: 'Create 1 objects' })).toBeDisabled();
  expect(screen.getByText(/motion engine accepts another:type/)).toBeVisible();
  expect(second.api.request).not.toHaveBeenCalled();
  fireEvent.change(screen.getByLabelText('Engine'), { target: { value: 'workflow' } });
  expect(screen.getByRole('button', { name: 'Create 1 objects' })).toBeEnabled();
});
