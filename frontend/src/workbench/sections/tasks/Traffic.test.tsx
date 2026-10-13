import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { I18nProvider } from '../../../i18n/I18nProvider';
import { IncidentsBlock, parseIncidentPosition } from './Traffic';
import type { Operation, OperationPreview } from '../operations';
import type { Project } from '../../api';

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
const config = { cooperation: { incidents: [{ id: 'fall-east', kind: 'person_fall', position: [20, 40, 0], at_s: 90 }] } };
const props = { config, api: {} as never, project, onProject, saveDraft, L, duration_s: 360 };
const element = (override: Partial<typeof props> = {}) => <I18nProvider><IncidentsBlock {...props} {...override} /></I18nProvider>;
beforeEach(() => {
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
    fireEvent.change(within(panel).getByLabelText('Time (s)'), { target: { value } });
    expect(within(panel).getByLabelText('Time (s)')).toHaveValue(value);
  }
  fireEvent.click(within(panel).getByRole('button', { name: 'Preview' }));
  await within(panel).findByRole('button', { name: 'Confirm' });
  view.rerender(element({ config: structuredClone(config) }));
  expect(within(panel).getByRole('button', { name: 'Confirm' })).toBeEnabled();
  expect(previewOperations.mock.calls[0][2][0]).toMatchObject({ op: 'set_incident', params: { id: 'fall-east', at: [20, 40, 0], time_s: 120.5 } });
  fireEvent.change(within(panel).getByLabelText('Time (s)'), { target: { value: '121' } });
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
  fireEvent.change(screen.getByLabelText('时间 (s)'), { target: { value: '-1' } });
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
