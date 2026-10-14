import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { I18nProvider } from '../../../i18n/I18nProvider';
import { IncidentsBlock, parseIncidentPosition } from './Traffic';
import type { Operation, OperationPreview } from '../operations';
import type { Project } from '../../api';
import type { Draft } from './model';

const L = (en: string, _zh: string) => en;
const onProject = vi.fn();
const project = { id: 'w1', revision: 7, scenario: {} } as Project;
const saveDraft = vi.fn(async () => project);
const previewOperations = vi.fn(async (_api: unknown, _project: unknown, operations: Operation[]): Promise<OperationPreview> =>
  ({ diff: { changed: operations.map(op => ({ id: String(op.params.id), fields: [{ name: 'time_s', before: 90, after: op.params.time_s }] })), added: [], removed: [] }, notes: [] }));
const applyOperations = vi.fn(async (_api: unknown, _project: unknown, _operations: Operation[]) => ({ ...project, revision: 8 }));
vi.mock('../operations', async (importOriginal: () => Promise<typeof import('../operations')>) => {
  const actual = await importOriginal();
  return { ...actual,
    previewOperations: (api: unknown, project: unknown, operations: Operation[]) => previewOperations(api, project, operations),
    applyOperations: (api: unknown, project: unknown, operations: Operation[]) => applyOperations(api, project, operations),
  };
});
const config: Draft = { cooperation: { incidents: [{ id: 'fall-east', kind: 'person_fall', position: [20, 40, 0], at_s: 90 }] } };
const props = { config, api: {} as never, project, onProject, saveDraft, L, duration_s: 360 };
const element = (override: Partial<typeof props> = {}) => <I18nProvider><IncidentsBlock {...props} {...override} /></I18nProvider>;
beforeEach(() => {
  sessionStorage.clear();
  window.localStorage.setItem('aeroagentsim.locale', 'en-US');
  onProject.mockClear(); saveDraft.mockClear(); previewOperations.mockClear(); applyOperations.mockClear();
});
afterEach(cleanup);

it('prefills the actual server position and previews an incident change across parent rerenders (P0)', async () => {
  const view = render(element());
  expect(screen.queryByRole('alert')).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: 'Change' }));
  const panel = screen.getByTestId('wb-incident-edit');
  expect(within(panel).getByLabelText('Incident position')).toHaveValue('20,40,0');
  for (const value of ['120', '120.', '120.5']) {
    fireEvent.change(within(panel).getByLabelText('Starts at (s)'), { target: { value } });
    expect(within(panel).getByLabelText('Starts at (s)')).toHaveValue(value);
  }
  fireEvent.click(within(panel).getByRole('button', { name: 'Preview' }));
  await within(panel).findByRole('button', { name: 'Confirm' });
  view.rerender(element({ config: structuredClone(config) }));
  expect(within(panel).getByRole('button', { name: 'Confirm' })).toBeEnabled();
  expect(previewOperations.mock.calls[0][2][0]).toMatchObject({ op: 'set_incident', params: { id: 'fall-east', at: [20, 40, 0], time_s: 120.5 } });
  fireEvent.change(within(panel).getByLabelText('Starts at (s)'), { target: { value: '121' } });
  expect(within(panel).getByRole('status')).toHaveTextContent('Out of date');
});

it('previews and confirms an incident add without a staging button', async () => {
  render(element());
  fireEvent.change(screen.getByLabelText('Incident ID'), { target: { value: 'fall-west' } });
  fireEvent.change(screen.getByLabelText('Incident position'), { target: { value: 'street-west' } });
  const form = screen.getByLabelText('Incident ID').closest('.wb-form-stack') as HTMLElement;
  fireEvent.click(within(form).getByRole('button', { name: 'Preview' }));
  await within(form).findByRole('button', { name: 'Confirm' });
  expect(previewOperations.mock.calls[0][2][0]).toMatchObject({ op: 'add_incident', params: { id: 'fall-west', kind: 'person_fall', at: 'street-west', time_s: 90 } });
  fireEvent.click(within(form).getByRole('button', { name: 'Confirm' }));
  await waitFor(() => expect(onProject).toHaveBeenCalled());
  expect(applyOperations.mock.calls[0][2][0].op).toBe('add_incident');
});

it('renders only Chinese numeric validation after touch (T4-R2-014)', () => {
  window.localStorage.setItem('aeroagentsim.locale', 'zh-CN');
  render(element({ L: (_en, zh) => zh }));
  expect(screen.queryByRole('alert')).toBeNull();
  fireEvent.change(screen.getByLabelText('开始时间 (s)'), { target: { value: '-1' } });
  expect(screen.getAllByRole('alert')).toHaveLength(1);
  expect(screen.getByRole('alert')).toHaveTextContent('不能小于 0。');
  expect(screen.queryByText(/Cannot be below/)).toBeNull();
  expect(previewOperations).not.toHaveBeenCalled();
});

it('rejects missing coordinates and invalid time without coercing empty values to zero', () => {
  render(element());
  fireEvent.change(screen.getByLabelText('Incident ID'), { target: { value: 'fall-invalid' } });
  fireEvent.change(screen.getByLabelText('Incident position'), { target: { value: '20,,0' } });
  expect(screen.getByRole('alert')).toHaveTextContent('three finite coordinates');
  const form = screen.getByLabelText('Incident ID').closest('.wb-form-stack') as HTMLElement;
  expect(within(form).getByRole('button', { name: 'Preview' })).toBeDisabled();
  expect(parseIncidentPosition('20,,0')).toBeUndefined();
  expect(parseIncidentPosition('20,40,0')).toEqual([20, 40, 0]);
  expect(previewOperations).not.toHaveBeenCalled();
});

it('offers a visible read-only explanation when editing is not wired', () => {
  render(<I18nProvider><IncidentsBlock config={config} L={L} /></I18nProvider>);
  expect(screen.getByText(/Open this form from the workbench/)).toBeTruthy();
  expect(screen.queryByRole('button', { name: 'Preview removal' })).toBeNull();
});

it('adds a fire incident with kind chip, kind radius default and an end time (spec §4)', async () => {
  const form = () => screen.getByLabelText('Incident ID').closest('.wb-form-stack') as HTMLElement;
  render(element());
  fireEvent.click(within(form()).getByRole('button', { name: 'Fire' }));
  expect(within(form()).getByLabelText('Affected radius (m)')).toHaveValue('30');
  expect(within(form()).getByText(/visual and scheduling marker/)).toBeTruthy();
  fireEvent.change(screen.getByLabelText('Incident ID'), { target: { value: 'fire-north' } });
  fireEvent.change(screen.getByLabelText('Incident position'), { target: { value: 'street-north' } });
  fireEvent.change(within(form()).getByLabelText('Ends at (s)'), { target: { value: '240' } });
  fireEvent.click(within(form()).getByRole('button', { name: 'Preview' }));
  await within(form()).findByRole('button', { name: 'Confirm' });
  expect(previewOperations.mock.calls[0][2][0]).toMatchObject({ op: 'add_incident', params: { id: 'fire-north', kind: 'fire', radius_m: 30, time_s: 90, end_s: 240 } });
  fireEvent.click(within(form()).getByRole('button', { name: 'Confirm' }));
  await waitFor(() => expect(applyOperations).toHaveBeenCalled());
});

it('lists incidents as "Fire · 01:30–04:00 · r 30 m" style rows and validates radius and end', () => {
  render(element({ config: { cooperation: { incidents: [
    { id: 'fire-1', kind: 'fire', position: [20, 40, 0], at_s: 90, end_s: 240, radius_m: 30 },
  ] } } }));
  expect(screen.getByTestId('wb-incident')).toHaveTextContent('Fire');
  expect(screen.getByTestId('wb-incident')).toHaveTextContent('01:30–04:00');
  expect(screen.getByTestId('wb-incident')).toHaveTextContent('r 30 m');
  fireEvent.click(screen.getByRole('button', { name: 'Change' }));
  const panel = screen.getByTestId('wb-incident-edit');
  fireEvent.change(within(panel).getByLabelText('Affected radius (m)'), { target: { value: '900' } });
  expect(screen.getByRole('alert')).toHaveTextContent('Cannot exceed 500.');
  expect(within(panel).getByRole('button', { name: 'Preview' })).toBeDisabled();
  fireEvent.change(within(panel).getByLabelText('Affected radius (m)'), { target: { value: '30' } });
  fireEvent.change(within(panel).getByLabelText('Ends at (s)'), { target: { value: '60' } });
  expect(screen.getByRole('alert')).toHaveTextContent('End must be after start.');
  expect(previewOperations).not.toHaveBeenCalled();
});

it('restores a closed incident editor independently of the staged new incident', () => {
  const first = render(element());
  fireEvent.change(screen.getByLabelText('Incident ID'), { target: { value: 'pending-new' } });
  fireEvent.change(screen.getByLabelText('Incident position'), { target: { value: '10,20,5' } });
  fireEvent.click(screen.getByRole('button', { name: 'Change' }));
  const editing = within(screen.getByTestId('wb-incident-edit'));
  fireEvent.change(editing.getByLabelText('Starts at (s)'), { target: { value: '120.5' } });
  fireEvent.change(editing.getByLabelText('Incident position'), { target: { value: '30,40,7' } });
  fireEvent.click(screen.getByRole('button', { name: 'Close' }));
  first.unmount();
  render(element());
  expect(screen.getByLabelText('Incident ID')).toHaveValue('pending-new');
  expect(screen.getByLabelText('Incident position')).toHaveValue('10,20,5');
  fireEvent.click(screen.getByRole('button', { name: 'Change' }));
  const reopened = within(screen.getByTestId('wb-incident-edit'));
  expect(reopened.getByLabelText('Starts at (s)')).toHaveValue('120.5');
  expect(reopened.getByLabelText('Incident position')).toHaveValue('30,40,7');
  expect(screen.getAllByText('Restored unsaved inputs')).toHaveLength(2);
  fireEvent.click(reopened.getByRole('button', { name: 'Discard' }));
  expect(reopened.getByLabelText('Starts at (s)')).toHaveValue('90');
  expect(screen.getByLabelText('Incident ID')).toHaveValue('pending-new');
});
