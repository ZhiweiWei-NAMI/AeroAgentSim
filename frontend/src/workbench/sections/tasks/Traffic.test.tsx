import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { I18nProvider } from '../../../i18n/I18nProvider';
import { IncidentsBlock } from './Traffic';
import type { Operation, OperationPreview } from '../operations';
import type { Project } from '../../api';

const L = (en: string) => en;
const onProject = vi.fn();
const project: Project = { id: 'w1', name: 'Test project', revision: 7, updated_at: '', latest_version: null, dirty: true, scenario: {} };
const saveDraft = vi.fn(async () => project);
const previewOperations = vi.fn(async (_api: unknown, _project: { id: string; revision: number }, operations: Operation[]): Promise<OperationPreview> =>
  ({ diff: { changed: operations.map(op => ({ label: op.op, fields: [{ name: 'time_s', before: 90, after: 120 }] })), added: [], removed: [] }, notes: [] }));
const applyOperations = vi.fn(async (_api: unknown, _project: unknown, _operations: Operation[]) => ({ ...project, revision: 8 }));

vi.mock('../operations', async (importOriginal: () => Promise<typeof import('../operations')>) => {
  const actual = await importOriginal();
  return {
    ...actual,
    previewOperations: (api: unknown, project: { id: string; revision: number }, operations: Operation[]) => previewOperations(api, project, operations),
    applyOperations: (api: unknown, project: unknown, operations: Operation[]) => applyOperations(api, project, operations),
  };
});

const api = {} as never;
const config = { cooperation: { incidents: [{ id: 'fall-east', kind: 'person_fall', at: 'street-east', at_s: 90 }] } };

beforeEach(() => {
  window.localStorage.setItem('aeroagentsim.locale', 'en-US');
  onProject.mockClear(); saveDraft.mockClear(); previewOperations.mockClear(); applyOperations.mockClear();
});
afterEach(cleanup);

const renderBlock = (overrides: Partial<Record<string, unknown>> = {}) => render(
  <I18nProvider><IncidentsBlock config={config} api={api} project={{ id: 'w1', revision: 7 } as never}
    onProject={onProject} saveDraft={saveDraft} L={L} {...overrides} /></I18nProvider>,
);

it('lists incidents read-only and never renders a Leaf/draft edit of cooperation.incidents', () => {
  renderBlock();
  expect(screen.getByTestId('wb-incident').textContent).toContain('fall-east');
  expect(document.querySelector('[data-path*="cooperation/incidents"]')).toBeNull();
});

it('stages a change as C11 set_incident and an add as add_incident, with dry run before apply', async () => {
  renderBlock();
  // Change flow → set_incident (scope to the edit panel: the add form reuses the same labels)
  fireEvent.click(screen.getByRole('button', { name: 'Change' }));
  const panel = screen.getByTestId('wb-incident-edit');
  fireEvent.change(panel.querySelector('input[aria-label="Incident position"]')!, { target: { value: '20,40,0' } });
  fireEvent.change(panel.querySelector('input[aria-label="Time (s)"]')!, { target: { value: '120' } });
  fireEvent.click(screen.getByRole('button', { name: 'Preview incident change' }));
  await waitFor(() => expect(previewOperations).toHaveBeenCalled());
  expect(previewOperations.mock.calls[0][2][0]).toMatchObject({ op: 'set_incident', params: { id: 'fall-east', at: [20, 40, 0], time_s: 120 } });
  fireEvent.click(screen.getByRole('button', { name: 'Confirm' }));
  await waitFor(() => expect(applyOperations).toHaveBeenCalled());
  expect(applyOperations.mock.calls[0][2][0].op).toBe('set_incident');
  expect(onProject).toHaveBeenCalled();

  // Add flow → add_incident
  fireEvent.change(screen.getByLabelText('Incident ID'), { target: { value: 'fall-west' } });
  const addRow = screen.getByLabelText('Incident position') as HTMLInputElement; // the only one outside the (closed) edit panel
  fireEvent.change(addRow, { target: { value: 'street-west' } });
  fireEvent.click(screen.getByRole('button', { name: 'Schedule incident' }));
  await waitFor(() => expect(previewOperations.mock.calls.length).toBe(2));
  expect(previewOperations.mock.calls[1][2][0]).toMatchObject({ op: 'add_incident', params: { id: 'fall-west', kind: 'person_fall', at: 'street-west' } });
});

it('rejects an invalid time inline instead of coercing it', () => {
  renderBlock();
  fireEvent.click(screen.getByRole('button', { name: 'Change' }));
  const panel = screen.getByTestId('wb-incident-edit');
  fireEvent.change(panel.querySelector('input[aria-label="Incident position"]')!, { target: { value: 'street-east' } });
  fireEvent.change(panel.querySelector('input[aria-label="Time (s)"]')!, { target: { value: '-5' } });
  expect(screen.getByRole('button', { name: 'Preview incident change' })).toBeDisabled();
  expect(screen.getAllByText(/Cannot be below 0/).length).toBeGreaterThan(0);
});

it('degrades to a read-only note when the operations client is not wired', () => {
  render(<I18nProvider><IncidentsBlock config={config} L={L} /></I18nProvider>);
  expect(screen.getByText(/operations client/)).toBeTruthy();
  expect(screen.queryByRole('button', { name: 'Remove' })).toBeNull();
});
