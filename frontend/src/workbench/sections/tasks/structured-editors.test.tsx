import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, expect, it, vi } from 'vitest';
import { I18nProvider } from '../../../i18n/I18nProvider';
import { OrderStreams } from './OrderStreams';
import { CooperationEditor } from './Cooperation';
import { WeatherBlock } from './Weather';
import type { Operation, OperationPreview } from '../operations';
import type { Project } from '../../api';

const L = (en: string, _zh: string) => en;
const edit = vi.fn();
const replaceDraft = vi.fn();
const onProject = vi.fn();
const project: Project = { id: 'w1', name: 'Test project', revision: 7, updated_at: '', latest_version: null, dirty: true, scenario: {} };
const saveDraft = vi.fn(async () => project);
const previewOperations = vi.fn(async (_api: unknown, _project: { id: string; revision: number }, operations: Operation[]): Promise<OperationPreview> =>
  ({ diff: { added: operations.map(op => ({ label: op.op })), changed: [], removed: [] }, notes: [] }));
const applyOperations = vi.fn(async (_api: unknown, _project: unknown, _operations: Operation[]) => ({ id: 'w1', revision: 8, scenario: {} }));

vi.mock('../operations', async (importOriginal: () => Promise<typeof import('../operations')>) => {
  const actual = await importOriginal();
  return {
    ...actual,
    previewOperations: (api: unknown, project: { id: string; revision: number }, operations: Operation[]) => previewOperations(api, project, operations),
    applyOperations: (api: unknown, project: unknown, operations: Operation[]) => applyOperations(api, project, operations),
  };
});

const api = {} as never;
const opProps = { api, project: { id: 'w1', revision: 7 } as never, onProject, saveDraft, zh: false, studioApi: {} as never, locale: 'en-US' as never, issues: [], reportValidity: vi.fn() };

beforeEach(() => {
  window.localStorage.setItem('aeroagentsim.locale', 'en-US');
  edit.mockClear(); replaceDraft.mockClear(); onProject.mockClear();
  saveDraft.mockClear(); previewOperations.mockClear(); applyOperations.mockClear();
});
const draft = {
  run: { until_ns: 360_000_000_000 },
  entities: [
    { id: 'kitchen-1', type: 'lowalt:Restaurant', facts: { 'lowalt.site_position': [0, 0, 0] } },
    { id: 'courier-1', type: 'lowalt:UAV', facts: { 'lowalt.soc': 90 } },
  ],
};
const config = { generators: [{ id: 'parcels', kind: 'parcel', tasks: ['parcels-1'], origins: ['depot'], destinations: ['customer-1'], arrivals: { mode: 'poisson', rate_per_s: 0.08, start_ns: 0, end_ns: 180_000_000_000 }, priority: 1, payload_kg: 1 }] };

function Streams() {
  return <I18nProvider><OrderStreams config={config} draft={draft} edit={edit} L={L} {...opProps} /></I18nProvider>;
}

it('stages an order stream as add_orders with facility endpoints and a seconds schedule', async () => {
  render(<Streams />);
  fireEvent.change(screen.getByLabelText('New order stream ID'), { target: { value: 'rush' } });
  fireEvent.change(screen.getByLabelText('From facility'), { target: { value: 'kitchen-1' } });
  fireEvent.change(screen.getByLabelText('To facility'), { target: { value: 'courier-1' } });
  fireEvent.click(screen.getByRole('button', { name: 'Stage order stream' }));
  await waitFor(() => expect(screen.getByRole('button', { name: 'Add order stream' })).toBeTruthy());
  fireEvent.click(screen.getByRole('button', { name: 'Add order stream' }));
  await waitFor(() => expect(previewOperations).toHaveBeenCalled());
  const ops = previewOperations.mock.calls[0][2];
  expect(ops[0].op).toBe('add_orders');
  expect(ops[0].params).toMatchObject({ id: 'rush', kind: 'parcel', count: 3, from: ['kitchen-1'], to: ['courier-1'], release: { mode: 'poisson' } });
  fireEvent.click(screen.getByRole('button', { name: 'Confirm' }));
  await waitFor(() => expect(applyOperations).toHaveBeenCalled());
  expect(applyOperations.mock.calls[0][2][0].op).toBe('add_orders');
  expect(onProject).toHaveBeenCalled();
  expect(edit).not.toHaveBeenCalled();
});

it('blocks the add until its prerequisites exist and shows the missing one inline', () => {
  render(<Streams />);
  expect(screen.getByRole('button', { name: 'Add order stream' })).toBeDisabled();
  expect(screen.getByText('Pick a start and an end facility.')).toBeTruthy();
  fireEvent.change(screen.getByLabelText('New order stream ID'), { target: { value: 'parcels' } });
  expect(screen.getByText('This stream ID is already used.')).toBeTruthy();
});

it('offers no dead end without facilities: the missing prerequisite is named inline', () => {
  render(<I18nProvider><OrderStreams config={config} draft={{ ...draft, entities: [] }} edit={edit} L={L} {...opProps} /></I18nProvider>);
  expect(screen.getByText('Add a facility (restaurant/depot) in Objects first.')).toBeTruthy();
  expect(screen.getByRole('button', { name: 'Add order stream' })).toBeDisabled();
});

it('removes a stream through a confirmed remove_orders dry run', async () => {
  render(<Streams />);
  fireEvent.click(screen.getByText('parcels'));
  fireEvent.click(screen.getByRole('button', { name: 'Remove stream…' }));
  expect(screen.getByText(/removes its 1 task\(s\)/)).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: 'Confirm removal' }));
  await waitFor(() => expect(previewOperations).toHaveBeenCalled());
  expect(previewOperations.mock.calls[0][2][0].op).toBe('remove_orders');
  fireEvent.click(screen.getByRole('button', { name: 'Confirm' }));
  await waitFor(() => expect(applyOperations).toHaveBeenCalled());
  expect(edit).not.toHaveBeenCalled();
});

it('shows the stored nanosecond release as seconds in the row editor', () => {
  render(<Streams />);
  fireEvent.click(screen.getByText('parcels'));
  expect(screen.getByDisplayValue('180')).toBeTruthy();
});

it('cooperation stages set_cooperation and removes with a confirmed remove_cooperation', async () => {
  const withCoop = { ...config, cooperation: { watchers: { vehicles: ['courier-1'], notice_radius_m: 150 }, responder: { mode: 'prescribed' }, helpers: { vehicles: [], mode: 'prescribed' }, incidents: [] } };
  render(<I18nProvider><CooperationEditor config={withCoop} engineName="tasks" draft={draft} edit={edit} L={L} replaceDraft={replaceDraft} {...opProps} /></I18nProvider>);
  fireEvent.change(screen.getByLabelText('Helper vehicles'), { target: { value: 'courier-1' } });
  fireEvent.click(screen.getByRole('button', { name: 'Stage responsibility change' }));
  await waitFor(() => expect(screen.getByRole('button', { name: 'Update cooperation' })).toBeTruthy());
  fireEvent.click(screen.getByRole('button', { name: 'Update cooperation' }));
  await waitFor(() => expect(previewOperations).toHaveBeenCalled());
  expect(previewOperations.mock.calls[0][2][0].op).toBe('set_cooperation');
  expect((previewOperations.mock.calls[0][2][0].params as { helpers: { vehicles: string[] } }).helpers.vehicles).toEqual(['courier-1']);
  fireEvent.click(screen.getByRole('button', { name: 'Confirm' }));
  await waitFor(() => expect(applyOperations).toHaveBeenCalled());
  fireEvent.click(screen.getByRole('button', { name: 'Remove cooperation…' }));
  fireEvent.click(screen.getByRole('button', { name: 'Confirm removal' }));
  await waitFor(() => expect(previewOperations).toHaveBeenCalledTimes(2));
  expect(previewOperations.mock.calls[1][2][0].op).toBe('remove_cooperation');
});

it('cooperation without vehicles names the missing prerequisite instead of a dead form', () => {
  render(<I18nProvider><CooperationEditor config={{ generators: [] }} engineName="tasks" draft={{ ...draft, entities: [] }} edit={edit} L={L} replaceDraft={replaceDraft} {...opProps} /></I18nProvider>);
  expect(screen.getByText(/Add UAVs/)).toBeTruthy();
  expect(screen.getByRole('button', { name: 'Set up cooperation' })).toBeDisabled();
});

it('weather adds wind and a gust through set_wind / add_gust with no direction field', async () => {
  const environment = { config: { profiles: { weather: { 'lowalt.wind': { mode: 'constant', value: [2, 0, 0] } } } } };
  const { rerender } = render(<I18nProvider><WeatherBlock environmentName="weather" environment={environment} draft={draft} edit={edit} L={L} {...opProps} /></I18nProvider>);
  expect(screen.queryByLabelText('Direction (°)')).toBeNull();
  fireEvent.change(screen.getByLabelText('Background speed (m/s)'), { target: { value: '5' } });
  fireEvent.click(screen.getByRole('button', { name: 'Set background wind' }));
  await waitFor(() => expect(previewOperations).toHaveBeenCalled());
  expect(previewOperations.mock.calls[0][2][0].op).toBe('set_wind');
  expect(previewOperations.mock.calls[0][2][0].params).toEqual({ speed_mps: 5 });
  fireEvent.click(screen.getByRole('button', { name: 'Stage gust' }));
  fireEvent.click(screen.getByRole('button', { name: 'Add gust' }));
  await waitFor(() => expect(previewOperations).toHaveBeenCalledTimes(2));
  expect(previewOperations.mock.calls[1][2][0].op).toBe('add_gust');
  expect(previewOperations.mock.calls[1][2][0].params).toEqual({ start_s: 60, end_s: 90, speed_mps: 9 });
  const updated = { config: { profiles: { weather: { 'lowalt.wind': { mode: 'gust', value: [5, 0, 0], gusts: [{ start_ns: 60_000_000_000, end_ns: 90_000_000_000, value: [9, 0, 0] }] } } } } };
  rerender(<I18nProvider><WeatherBlock environmentName="weather" environment={updated} draft={draft} edit={edit} L={L} {...opProps} /></I18nProvider>);
  expect(screen.getByText('60–90 s')).toBeTruthy();
  expect(screen.getByText('9 m/s')).toBeTruthy();
});

it('weather rejects inverted, out-of-horizon or negative gusts visibly and never writes defaults', () => {
  const environment = { config: { profiles: { weather: { 'lowalt.wind': { mode: 'constant', value: [2, 0, 0] } } } } };
  render(<I18nProvider><WeatherBlock environmentName="weather" environment={environment} draft={draft} edit={edit} L={L} {...opProps} /></I18nProvider>);
  fireEvent.change(screen.getByLabelText('Start (s)'), { target: { value: '200' } });
  fireEvent.change(screen.getByLabelText('End (s)'), { target: { value: '170' } });
  fireEvent.click(screen.getByRole('button', { name: 'Stage gust' }));
  expect(screen.getByText('End must be after start.')).toBeTruthy();
  expect(screen.queryByRole('button', { name: 'Add gust' })).toBeNull();
  fireEvent.change(screen.getByLabelText('Start (s)'), { target: { value: '300' } });
  fireEvent.change(screen.getByLabelText('End (s)'), { target: { value: '400' } });
  fireEvent.click(screen.getByRole('button', { name: 'Stage gust' }));
  expect(screen.getByText(/within the run duration \(360 s\)/)).toBeTruthy();
  fireEvent.change(screen.getByLabelText('Start (s)'), { target: { value: '10' } });
  fireEvent.change(screen.getByLabelText('End (s)'), { target: { value: '20' } });
  fireEvent.change(screen.getByLabelText('Speed (m/s)'), { target: { value: '-1' } });
  fireEvent.click(screen.getByRole('button', { name: 'Stage gust' }));
  expect(screen.getByText(/0 m\/s or more/)).toBeTruthy();
  expect(edit).not.toHaveBeenCalled();
});
