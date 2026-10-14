import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { I18nProvider } from '../../../i18n/I18nProvider';
import { OrderStreams } from './OrderStreams';
import { CooperationEditor } from './Cooperation';
import { WeatherBlock } from './Weather';
import { ComponentsSection } from '../ComponentsSection';
import { TasksSection } from '../TasksSection';
import type { Operation, OperationPreview } from '../operations';
import type { Project } from '../../api';

const L = (en: string, _zh: string) => en;
const edit = vi.fn(), replaceDraft = vi.fn(), onProject = vi.fn();
const project = { id: 'w1', name: 'Test project', revision: 7, updated_at: '', latest_version: null, dirty: true, scenario: {} } as Project;
const saveDraft = vi.fn(async () => project);
const previewOperations = vi.fn(async (_api: unknown, _project: unknown, operations: Operation[]): Promise<OperationPreview> =>
  ({ diff: { added: operations.map(op => ({ id: String(op.params.id ?? op.op), label: op.op })), changed: [], removed: [] }, notes: [] }));
const applyOperations = vi.fn(async (_api: unknown, _project: unknown, _operations: Operation[]) => ({ ...project, revision: 8 }));

vi.mock('../operations', async (importOriginal: () => Promise<typeof import('../operations')>) => {
  const actual = await importOriginal();
  return { ...actual,
    previewOperations: (api: unknown, project: unknown, operations: Operation[]) => previewOperations(api, project, operations),
    applyOperations: (api: unknown, project: unknown, operations: Operation[]) => applyOperations(api, project, operations),
  };
});

const opProps = { api: {} as never, project, onProject, saveDraft, zh: false };
const sectionProps = { ...opProps, studioApi: {} as never, locale: 'en-US' as const, L, edit, replaceDraft, issues: [], reportValidity: vi.fn() };
const draft = {
  run: { until_ns: 360_000_000_000, advance_ns: 1_000_000_000 },
  entities: [
    { id: 'kitchen-1', type: 'lowalt:Restaurant', facts: { 'lowalt.site_position': [0, 0, 0] } },
    { id: 'customer-1', type: 'lowalt:Customer', facts: { 'lowalt.site_position': [10, 0, 0] } },
    { id: 'handoff-east', type: 'lowalt:HandoffPoint', facts: { 'lowalt.site_position': [20, 0, 0] } },
    { id: 'courier-1', type: 'lowalt:UAV', facts: { 'lowalt.soc': 90 } },
  ],
};
const stream = { id: 'parcels', kind: 'parcel', tasks: ['parcels-1'], origins: ['kitchen-1'], destinations: ['customer-1'], arrivals: { mode: 'poisson', rate_per_s: 0.08, start_ns: 1_500_000_000, end_ns: 180_000_000_000 }, priority: 1, payload_kg: 1 };
const config = { generators: [stream] };
const environment = { config: { profiles: { weather: { 'lowalt.wind': { mode: 'constant', value: [0, 0, 0] } } } } };

it('forwards section navigation into task prerequisites', () => {
  const goTo = vi.fn();
  render(<I18nProvider><TasksSection {...sectionProps} goTo={goTo} draft={{ ...draft, entities: [], engines: { tasks: { plugin: 'lowalt_tasks', config: { generators: [] } } } }} /></I18nProvider>);
  fireEvent.click(screen.getByRole('button', { name: 'Add place' }));
  expect(goTo).toHaveBeenCalledWith('objects', 'add-place');
});

it('reviews gust removal before confirming and sends the reviewed selector', async () => {
  const configured = { config: { profiles: { weather: { 'lowalt.wind': { mode: 'gust', value: [0, 0, 0], gusts: [{ start_ns: 20_000_000_000, end_ns: 25_000_000_000, value: [9, 0, 0] }] } } } } };
  previewOperations.mockResolvedValueOnce({ diff: { added: [], changed: [], removed: [{ kind: 'event', id: 'wind-gust-20000000000', label: 'Wind gust: 9 m/s' }] }, notes: [] });
  render(weather({ environment: configured }));
  fireEvent.click(screen.getByRole('button', { name: 'Remove' }));
  const confirm = await screen.findByRole('button', { name: 'Confirm' });
  expect(screen.getByRole('region', { name: 'Removed' })).toHaveTextContent('Wind gust: 9 m/s');
  expect(applyOperations).not.toHaveBeenCalled();
  fireEvent.click(confirm);
  await waitFor(() => expect(applyOperations).toHaveBeenCalled());
  expect(applyOperations.mock.calls[0][2][0]).toMatchObject({ op: 'remove_gust', params: { start_s: 20 } });
});
beforeEach(() => {
  sessionStorage.clear();
  window.localStorage.setItem('aeroagentsim.locale', 'en-US');
  edit.mockClear(); replaceDraft.mockClear(); onProject.mockClear(); saveDraft.mockClear(); previewOperations.mockClear(); applyOperations.mockClear();
});
afterEach(cleanup);

const streams = (override: Partial<Parameters<typeof OrderStreams>[0]> = {}) => <I18nProvider><OrderStreams config={config} draft={draft} edit={edit} L={L} {...opProps} {...override} /></I18nProvider>;
const creation = () => screen.getByLabelText('New order stream ID').closest('.wb-order-add') as HTMLElement;
const fillOrder = () => {
  fireEvent.change(screen.getByLabelText('New order stream ID'), { target: { value: 'rush' } });
  fireEvent.change(screen.getByLabelText('From facility'), { target: { value: 'kitchen-1' } });
  fireEvent.change(screen.getByLabelText('To facility'), { target: { value: 'customer-1' } });
};

it('restores a complete staged order setup after close/refresh without applying it (T1-R3-003)', () => {
  const first = render(streams()); fillOrder();
  fireEvent.change(screen.getByLabelText('New order stream ID'), { target: { value: 'research-draft' } });
  fireEvent.change(screen.getByLabelText('New order kind'), { target: { value: 'food' } });
  fireEvent.change(screen.getByLabelText('Task count'), { target: { value: '7' } });
  fireEvent.change(screen.getByLabelText('Rate (1/min)'), { target: { value: '12' } });
  first.unmount();
  render(streams());
  expect(screen.getByLabelText('New order stream ID')).toHaveValue('research-draft');
  expect(screen.getByLabelText('New order kind')).toHaveValue('food');
  expect(screen.getByLabelText('Task count')).toHaveValue('7');
  expect(screen.getByLabelText('From facility')).toHaveValue('kitchen-1');
  expect(screen.getByLabelText('To facility')).toHaveValue('customer-1');
  expect(screen.getByLabelText('Rate (1/min)')).toHaveValue('12');
  expect(screen.getByText('Restored unsaved inputs')).toBeTruthy();
  expect(previewOperations).not.toHaveBeenCalled();
  expect(applyOperations).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: 'Discard' }));
  expect(screen.getByLabelText('New order stream ID')).toHaveValue('');
  expect(Object.keys(sessionStorage)).toHaveLength(0);
});

it('previews orders in one action and keeps the review across equal parent inputs (P0 previews / D5)', async () => {
  const view = render(streams());
  expect(screen.queryByRole('alert')).toBeNull();
  fillOrder();
  expect(screen.queryByText('Stage order stream')).toBeNull();
  fireEvent.click(within(creation()).getByRole('button', { name: 'Preview' }));
  await within(creation()).findByRole('button', { name: 'Confirm' });
  view.rerender(streams({ draft: structuredClone(draft), config: structuredClone(config) }));
  expect(within(creation()).getByRole('button', { name: 'Confirm' })).toBeEnabled();
  expect(previewOperations.mock.calls[0][2][0]).toMatchObject({ op: 'add_orders', params: { id: 'rush', count: 3, from: ['kitchen-1'], to: ['customer-1'], release: { start_s: 0, end_s: 360 } } });
  fireEvent.change(within(creation()).getByLabelText('Payload (kg)'), { target: { value: '2' } });
  expect(within(creation()).getByRole('status')).toHaveTextContent('Out of date');
  expect(within(creation()).queryByRole('button', { name: 'Confirm' })).toBeNull();
});

it('keeps 1.5-second starts while typing and sends exactly 1.5 (T4-R2-003)', async () => {
  render(streams()); fillOrder();
  for (const value of ['', '1', '1.', '1.5']) {
    fireEvent.change(screen.getByLabelText('Start (s)'), { target: { value } });
    expect(screen.getByLabelText('Start (s)')).toHaveValue(value);
  }
  for (const value of ['', '0', '0.', '0.5']) {
    fireEvent.change(screen.getByLabelText('Rate (1/min)'), { target: { value } });
    expect(screen.getByLabelText('Rate (1/min)')).toHaveValue(value);
  }
  fireEvent.click(within(creation()).getByRole('button', { name: 'Preview' }));
  await within(creation()).findByRole('button', { name: 'Confirm' });
  expect(previewOperations.mock.calls[0][2][0].params.release).toEqual({ mode: 'poisson', rate_per_s: 0.5 / 60, start_s: 1.5, end_s: 360 });
  fireEvent.click(within(creation()).getByRole('button', { name: 'Confirm' }));
  await waitFor(() => expect(applyOperations).toHaveBeenCalled());
  expect(applyOperations.mock.calls[0][2][0].params.release).toMatchObject({ start_s: 1.5 });
  expect(onProject).toHaveBeenCalled(); expect(edit).not.toHaveBeenCalled();
});

it.each([['Rate (1/min)', '0'], ['Start (s)', 'abc'], ['End (s)', ''], ['Priority', '1.5'], ['Payload (kg)', 'NaN'], ['Task count', '1001']])('visibly rejects invalid %s without sending a default', (label, value) => {
  render(streams()); fillOrder();
  fireEvent.change(screen.getByLabelText(label), { target: { value } });
  expect(within(creation()).getByRole('button', { name: 'Preview' })).toBeDisabled();
  expect(within(creation()).getByRole('alert')).toBeTruthy();
  expect(previewOperations).not.toHaveBeenCalled();
});

it('rejects invalid scheduled entries without silently filtering them', async () => {
  render(streams()); fillOrder();
  fireEvent.change(screen.getByLabelText('Release mode'), { target: { value: 'scheduled' } });
  const times = screen.getByLabelText('Release times in seconds, one per line');
  fireEvent.change(times, { target: { value: '0\ninvalid\n2.5' } });
  expect(times).toHaveValue('0\ninvalid\n2.5');
  expect(within(creation()).getByRole('button', { name: 'Preview' })).toBeDisabled();
  fireEvent.change(times, { target: { value: '0\n1.5\n2.5' } });
  fireEvent.click(within(creation()).getByRole('button', { name: 'Preview' }));
  await within(creation()).findByRole('button', { name: 'Confirm' });
  expect(previewOperations.mock.calls[0][2][0].params.release).toEqual({ mode: 'scheduled', times_s: [0, 1.5, 2.5] });
});

it('offers actual facility choices and an inline Objects navigation action (D6)', () => {
  const goTo = vi.fn();
  render(streams({ draft: { ...draft, entities: [draft.entities[3]] }, goTo }));
  expect(screen.getByLabelText('From facility')).toBeDisabled();
  expect(screen.getByLabelText('To facility')).toBeDisabled();
  expect(screen.queryByRole('option', { name: 'courier-1' })).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: 'Add place' }));
  expect(goTo).toHaveBeenCalledWith('objects', 'add-place');
  expect(within(creation()).getByRole('button', { name: 'Preview' })).toBeDisabled();
});

it('requires a real unique ID before removal and resolves id-less meal templates (T4-R2-011)', async () => {
  const template = { ...stream, id: undefined, kind: 'food', tasks: ['meal-1', 'meal-2', 'meal-3', 'meal-4', 'meal-5', 'meal-6'] };
  delete (template as Partial<typeof template>).id;
  render(streams({ config: { generators: [template] } }));
  expect(screen.queryByRole('button', { name: 'Preview removal' })).toBeNull();
  expect(screen.queryByText(/undefined/)).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: /meal.*Meal.*6 tasks/ }));
  fireEvent.click(screen.getByRole('button', { name: 'Remove stream…' }));
  expect(previewOperations).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: 'Preview removal' }));
  await screen.findByRole('button', { name: 'Confirm' });
  expect(previewOperations.mock.calls[0][2][0]).toMatchObject({ op: 'remove_orders', params: { id: 'meal' } });
  fireEvent.click(screen.getByRole('button', { name: 'Confirm' }));
  await waitFor(() => expect(applyOperations).toHaveBeenCalled());
  expect(applyOperations.mock.calls[0][2][0].params).toEqual({ id: 'meal' });
});

it.each([{ kind: 'parcel' }, { id: null, tasks: ['meal-1', 'meal-2'] }])('disables Remove when a stream has no valid ID (T4-R2-011)', broken => {
  render(streams({ config: { generators: [broken] } }));
  fireEvent.click(screen.getByRole('button', { name: /Stream 1/ }));
  expect(screen.getByRole('button', { name: 'Remove stream…' })).toBeDisabled();
  expect(screen.queryByRole('button', { name: 'Preview removal' })).toBeNull();
});

it('shows nanoseconds as seconds and edits existing streams through set_orders with raw decimals', async () => {
  render(streams());
  fireEvent.click(screen.getByRole('button', { name: /parcels.*Parcel.*1 tasks/ }));
  const row = screen.getByTestId('wb-order-row');
  expect(within(row).getByLabelText('Start (s)')).toHaveValue('1.5');
  expect(within(row).getByLabelText('End (s)')).toHaveValue('180');
  for (const value of ['2', '2.', '2.5']) {
    fireEvent.change(within(row).getByLabelText('Payload (kg)'), { target: { value } });
    expect(within(row).getByLabelText('Payload (kg)')).toHaveValue(value);
  }
  fireEvent.click(within(row).getByRole('button', { name: 'Preview' }));
  await within(row).findByRole('button', { name: 'Confirm' });
  expect(previewOperations.mock.calls[0][2][0]).toMatchObject({ op: 'set_orders', params: { id: 'parcels', payload_kg: 2.5, release: { start_s: 1.5, end_s: 180 } } });
  expect(previewOperations.mock.calls[0][2][0].params).not.toHaveProperty('kind');
  expect(edit).not.toHaveBeenCalled();
});

const cooperation = (override: Partial<Parameters<typeof CooperationEditor>[0]> = {}) => <I18nProvider><CooperationEditor config={{ generators: [] }} engineName="tasks" draft={draft} edit={edit} replaceDraft={replaceDraft} L={L} {...opProps} {...override} /></I18nProvider>;

it('restores staged cooperation participants, radius and goal after the editor unmounts', () => {
  const first = render(cooperation());
  fireEvent.change(screen.getByLabelText('Watcher vehicles'), { target: { value: 'courier-1' } });
  fireEvent.change(screen.getByLabelText('Notice radius (m)'), { target: { value: '180.' } });
  fireEvent.change(screen.getByLabelText('Handoff points'), { target: { value: 'handoff-east' } });
  fireEvent.change(screen.getByLabelText('Response mode'), { target: { value: 'agent' } });
  fireEvent.change(screen.getByLabelText('Response goal'), { target: { value: 'Investigate the actual incident' } });
  first.unmount();
  render(cooperation());
  expect(screen.getByLabelText('Watcher vehicles')).toHaveValue(['courier-1']);
  expect(screen.getByLabelText('Notice radius (m)')).toHaveValue('180.');
  expect(screen.getByLabelText('Handoff points')).toHaveValue(['handoff-east']);
  expect(screen.getByLabelText('Response goal')).toHaveValue('Investigate the actual incident');
  expect(screen.getByText('Restored unsaved inputs')).toBeTruthy();
  expect(previewOperations).not.toHaveBeenCalled();
});
const coopForm = () => screen.getByLabelText('Watcher vehicles').closest('.wb-form-stack') as HTMLElement;

it('assigns a real handoff point and retains cooperation preview across parent rerenders (T1-012 / P0)', async () => {
  const view = render(cooperation());
  fireEvent.change(screen.getByLabelText('Watcher vehicles'), { target: { value: 'courier-1' } });
  fireEvent.change(screen.getByLabelText('Handoff points'), { target: { value: 'handoff-east' } });
  fireEvent.click(within(coopForm()).getByRole('button', { name: 'Preview' }));
  await within(coopForm()).findByRole('button', { name: 'Confirm' });
  view.rerender(cooperation({ draft: structuredClone(draft) }));
  expect(within(coopForm()).getByRole('button', { name: 'Confirm' })).toBeEnabled();
  expect(previewOperations.mock.calls[0][2][0]).toMatchObject({ op: 'set_cooperation', params: { watchers: { vehicles: ['courier-1'], notice_radius_m: 150 }, handoff_points: ['handoff-east'] } });
  fireEvent.change(screen.getByLabelText('Notice radius (m)'), { target: { value: '150.5' } });
  expect(within(coopForm()).getByRole('status')).toHaveTextContent('Out of date');
});

it('prefills current cooperation assignments and can review their removal', async () => {
  const current = { ...config, cooperation: { watchers: { vehicles: ['courier-1'], notice_radius_m: 123 }, helpers: { vehicles: ['courier-1'], ask_radius_m: 456, selection: 'first' }, handoff_points: [{ id: 'handoff-east', position: [20, 0, 0] }] } };
  render(cooperation({ config: current }));
  expect(screen.getByLabelText('Notice radius (m)')).toHaveValue('123');
  expect(screen.getByLabelText('Ask radius (m)')).toHaveValue('456');
  expect(screen.getByLabelText('Watcher vehicles')).toHaveValue(['courier-1']);
  expect(screen.getByLabelText('Handoff points')).toHaveValue(['handoff-east']);
  fireEvent.click(screen.getByRole('button', { name: 'Preview cooperation removal' }));
  await screen.findByRole('button', { name: 'Confirm' });
  expect(previewOperations.mock.calls[0][2][0].op).toBe('remove_cooperation');
  expect(edit).not.toHaveBeenCalled();
});

it('shows handoff and UAV prerequisites inline and rejects invalid numeric assignments', () => {
  render(cooperation({ draft: { ...draft, entities: [] } }));
  expect(screen.getByLabelText('Watcher vehicles')).toBeDisabled();
  expect(screen.getByLabelText('Handoff points')).toBeDisabled();
  expect(screen.getByText(/No handoff-point places yet/)).toBeTruthy();
  expect(within(coopForm()).getByRole('button', { name: 'Preview' })).toBeDisabled();
  expect(screen.queryByRole('alert')).toBeNull();
});

const weather = (override: Partial<Parameters<typeof WeatherBlock>[0]> = {}) => <I18nProvider><WeatherBlock environmentName="weather" environment={environment} draft={draft} edit={edit} L={L} {...opProps} {...override} /></I18nProvider>;

it('restores background wind and gust inputs independently; applying background wind keeps the pending gust', async () => {
  const first = render(weather());
  fireEvent.change(screen.getByLabelText('Background speed (m/s)'), { target: { value: '3.5' } });
  fireEvent.change(screen.getByLabelText('Start (s)'), { target: { value: '10' } });
  fireEvent.change(screen.getByLabelText('End (s)'), { target: { value: '20' } });
  fireEvent.change(screen.getByLabelText('Speed (m/s)'), { target: { value: '5.5' } });
  first.unmount();
  render(weather());
  expect(screen.getByLabelText('Background speed (m/s)')).toHaveValue('3.5');
  expect(screen.getByLabelText('Start (s)')).toHaveValue('10');
  expect(screen.getByLabelText('End (s)')).toHaveValue('20');
  expect(screen.getByLabelText('Speed (m/s)')).toHaveValue('5.5');
  expect(screen.getAllByText('Restored unsaved inputs')).toHaveLength(2);
  fireEvent.click(within(windForm()).getByRole('button', { name: 'Preview' }));
  fireEvent.click(await within(windForm()).findByRole('button', { name: 'Confirm' }));
  await waitFor(() => expect(onProject).toHaveBeenCalledOnce());
  const buffers = Object.keys(sessionStorage).map(key => JSON.parse(sessionStorage.getItem(key)!));
  expect(buffers.map(buffer => buffer.form)).toEqual(['weather.gust:weather']);
  expect(screen.getByLabelText('Speed (m/s)')).toHaveValue('5.5');
});
const windForm = () => screen.getByLabelText('Background speed (m/s)').closest('fieldset') as HTMLElement;
const gustForm = () => screen.getByLabelText('Start (s)').closest('fieldset') as HTMLElement;

it('prefills background wind and keeps its review through an unchanged poll (P0 / D5)', async () => {
  const view = render(weather());
  expect(screen.getByLabelText('Background speed (m/s)')).toHaveValue('0');
  expect(screen.queryByRole('alert')).toBeNull();
  fireEvent.change(screen.getByLabelText('Background speed (m/s)'), { target: { value: '5.5' } });
  fireEvent.click(within(windForm()).getByRole('button', { name: 'Preview' }));
  await within(windForm()).findByRole('button', { name: 'Confirm' });
  view.rerender(weather({ environment: structuredClone(environment), draft: structuredClone(draft) }));
  expect(within(windForm()).getByRole('button', { name: 'Confirm' })).toBeEnabled();
  expect(previewOperations.mock.calls[0][2][0].params).toEqual({ speed_mps: 5.5 });
  fireEvent.change(screen.getByLabelText('Background speed (m/s)'), { target: { value: '6' } });
  expect(within(windForm()).getByRole('status')).toHaveTextContent('Out of date');
});

it('previews a gust directly with defaults inside the run and keeps Confirm after rerender (P0 / D5)', async () => {
  const view = render(weather());
  expect(screen.getByLabelText('Start (s)')).toHaveValue('90');
  expect(screen.getByLabelText('End (s)')).toHaveValue('180');
  expect(screen.queryByText('Stage gust')).toBeNull();
  fireEvent.click(within(gustForm()).getByRole('button', { name: 'Preview' }));
  await within(gustForm()).findByRole('button', { name: 'Confirm' });
  view.rerender(weather());
  expect(within(gustForm()).getByRole('button', { name: 'Confirm' })).toBeEnabled();
  expect(previewOperations.mock.calls[0][2][0]).toMatchObject({ op: 'add_gust', params: { start_s: 90, end_s: 180, speed_mps: 9 } });
  fireEvent.change(screen.getByLabelText('End (s)'), { target: { value: '190' } });
  expect(within(gustForm()).getByRole('status')).toHaveTextContent('Out of date');
});

it('rejects inverted, out-of-horizon and negative gusts after touch without writing defaults', () => {
  render(weather());
  fireEvent.change(screen.getByLabelText('Start (s)'), { target: { value: '200' } });
  expect(screen.getByRole('alert')).toHaveTextContent('End must be after start.');
  expect(within(gustForm()).getByRole('button', { name: 'Preview' })).toBeDisabled();
  fireEvent.change(screen.getByLabelText('End (s)'), { target: { value: '400' } });
  expect(screen.getByRole('alert')).toHaveTextContent('within the run duration (360 s)');
  fireEvent.change(screen.getByLabelText('Start (s)'), { target: { value: '10' } });
  fireEvent.change(screen.getByLabelText('End (s)'), { target: { value: '20' } });
  fireEvent.change(screen.getByLabelText('Speed (m/s)'), { target: { value: '-1' } });
  expect(screen.getByRole('alert')).toHaveTextContent('0 m/s or more');
  expect(previewOperations).not.toHaveBeenCalled(); expect(edit).not.toHaveBeenCalled();
});

it('gives short durations an inline Run navigation action (D5)', () => {
  const goTo = vi.fn();
  render(weather({ draft: { ...draft, run: { until_ns: 2_000_000_000, advance_ns: 1_000_000_000 } }, goTo }));
  expect(screen.getByText(/Run duration is 2 s/)).toBeTruthy();
  expect(screen.getByLabelText('Start (s)')).toHaveValue('0.5');
  expect(screen.getByLabelText('End (s)')).toHaveValue('1');
  fireEvent.click(screen.getByRole('button', { name: 'Run & recording' }));
  expect(goTo).toHaveBeenCalledWith('run', undefined);
});

it('keeps the Components bootstrap review after polling (T2-R2-001)', async () => {
  const element = () => <I18nProvider><ComponentsSection {...sectionProps} draft={{ run: draft.run, entities: [] }} /></I18nProvider>;
  const view = render(element());
  const card = screen.getByTestId('wb-lowalt-missing');
  fireEvent.click(within(card).getByRole('button', { name: 'Preview' }));
  await within(card).findByRole('button', { name: 'Confirm' });
  view.rerender(element());
  expect(within(card).getByRole('button', { name: 'Confirm' })).toBeEnabled();
  expect(previewOperations.mock.calls[0][2][0].op).toBe('add_lowalt_components');
});

it('shows task choices in the active locale (T4-R2-014)', () => {
  window.localStorage.setItem('aeroagentsim.locale', 'zh-CN');
  const Chinese = (_en: string, zh: string) => zh;
  render(streams({ L: Chinese, zh: true }));
  expect(screen.getByRole('option', { name: '餐食' })).toHaveValue('food');
  expect(screen.getByRole('option', { name: '事件响应' })).toHaveValue('incident_response');
  expect(screen.queryByRole('option', { name: 'food' })).toBeNull();
});

const conditionsForm = () => screen.getByTestId('wb-rain-presets').closest('fieldset') as HTMLElement;
const chip = (group: string, name: string) => within(screen.getByTestId(group)).getByRole('button', { name });

it('sends one set_weather with rain, visibility and light presets, and reads current values from the draft (P0)', async () => {
  const view = render(weather({ environment: { config: { profiles: { weather: {
    'lowalt.wind': { mode: 'constant', value: [0, 0, 0] },
    'lowalt.precipitation': { mode: 'constant', value: 4 / 3600 },
    'lowalt.visibility': { mode: 'constant', value: 500 },
  } } } } }));
  expect(chip('wb-rain-presets', 'Moderate · 4 mm/h')).toHaveAttribute('aria-pressed', 'true');
  expect(chip('wb-visibility-presets', 'Fog · 500 m')).toHaveAttribute('aria-pressed', 'true');
  fireEvent.click(chip('wb-rain-presets', 'Heavy · 10 mm/h'));
  fireEvent.click(chip('wb-visibility-presets', 'Mist · 2 km'));
  fireEvent.click(chip('wb-light-presets', 'Dusk'));
  fireEvent.click(within(conditionsForm()).getByRole('button', { name: 'Preview' }));
  await within(conditionsForm()).findByRole('button', { name: 'Confirm' });
  expect(previewOperations.mock.calls[0][2][0]).toMatchObject({ op: 'set_weather', params: { precipitation_mm_h: 10, visibility_m: 2000, light: 'dusk' } });
  view.rerender(weather());
  expect(within(conditionsForm()).getByRole('button', { name: 'Confirm' })).toBeEnabled();
  fireEvent.click(within(conditionsForm()).getByRole('button', { name: 'Confirm' }));
  await waitFor(() => expect(onProject).toHaveBeenCalled());
});

it('writes explicit zero and clear values and includes the visual-only note', async () => {
  render(weather());
  expect(screen.getByText(/Visual only — shown in the 3D view/)).toBeTruthy();
  expect(chip('wb-rain-presets', 'None')).toHaveAttribute('aria-pressed', 'true');
  expect(chip('wb-visibility-presets', 'Clear')).toHaveAttribute('aria-pressed', 'true');
  fireEvent.click(within(conditionsForm()).getByRole('button', { name: 'Preview' }));
  await within(conditionsForm()).findByRole('button', { name: 'Confirm' });
  expect(previewOperations.mock.calls[0][2][0]).toMatchObject({ op: 'set_weather', params: { precipitation_mm_h: 0, visibility_m: null, light: 'day' } });
});

it('sends from_deg only when the user changed the wind direction', async () => {
  render(weather({ environment: { config: { profiles: { weather: { 'lowalt.wind': { mode: 'constant', value: [6 / Math.SQRT2, -6 / Math.SQRT2, 0] } } } } } }));
  expect(screen.getByLabelText('From (°)')).toHaveValue('315');
  expect(screen.getByRole('button', { name: 'From north-west' })).toHaveAttribute('aria-pressed', 'true');
  fireEvent.click(within(windForm()).getByRole('button', { name: 'Preview' }));
  await within(windForm()).findByRole('button', { name: 'Confirm' });
  expect(previewOperations.mock.calls[0][2][0].params).toEqual({ speed_mps: 6 });
  fireEvent.click(screen.getByRole('button', { name: 'From south-west' }));
  expect(screen.getByLabelText('From (°)')).toHaveValue('225');
  fireEvent.click(within(windForm()).getByRole('button', { name: 'Preview again' }));
  await within(windForm()).findByRole('button', { name: 'Confirm' });
  expect(previewOperations.mock.calls[1][2][0].params).toEqual({ speed_mps: 6, from_deg: 225 });
});
