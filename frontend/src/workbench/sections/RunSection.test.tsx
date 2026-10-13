import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { I18nProvider } from '../../i18n/I18nProvider';
import { RunSection } from './RunSection';
import type { Operation, OperationPreview } from './operations';
import type { Project, WorkbenchApi } from '../api';

const L = (en: string, _zh: string) => en;
const edit = vi.fn(), onProject = vi.fn();
const draft = { run: { until_ns: 300_000_000_000, advance_ns: 10_000_000_000, seed: 42 }, engines: { tasks: { plugin: 'lowalt_tasks', config: {} } }, outputs: { path: 'recordings' } };
const project: Project = { id: 'w1', name: 'Test project', revision: 7, updated_at: '', latest_version: null, dirty: true, scenario: draft };
const saveDraft = vi.fn(async () => project);
const save = vi.fn(async (_id: string, scenario: Record<string, unknown>, _revision: number) => ({ ...project, revision: 8, scenario }));
const previewOperations = vi.fn(async (_api: unknown, _project: unknown, operations: Operation[]): Promise<OperationPreview> =>
  ({ diff: { added: [], changed: [{ kind: 'scene', id: 'duration_s', label: 'Duration', label_zh: '运行时长', fields: [{ name: 'duration_s', before: 300, after: operations[0].params.seconds }] }], removed: [] }, notes: [] }));
const applyOperations = vi.fn(async (_api: unknown, _project: unknown, operations: Operation[]) => ({ ...project, revision: 8, scenario: { ...draft, run: { ...draft.run, until_ns: Number(operations[0].params.seconds) * 1e9 } } }));
vi.mock('./operations', async (importOriginal: () => Promise<typeof import('./operations')>) => {
  const actual = await importOriginal();
  return { ...actual,
    previewOperations: (api: unknown, project: unknown, operations: Operation[]) => previewOperations(api, project, operations),
    applyOperations: (api: unknown, project: unknown, operations: Operation[]) => applyOperations(api, project, operations),
  };
});

const api = { save } as unknown as WorkbenchApi;
const props = { api, studioApi: {} as never, project, edit, zh: false, locale: 'en-US' as const, L, issues: [], onProject, saveDraft, reportValidity: vi.fn(), replaceDraft: vi.fn(), draft };
const element = (override: Partial<typeof props> = {}) => <I18nProvider><RunSection {...props} {...override} /></I18nProvider>;
const durationForm = () => screen.getByLabelText('Duration (s)').closest('fieldset') as HTMLElement;
const settingsForm = () => screen.getByLabelText('Random seed').closest('fieldset') as HTMLElement; // same card: Run timing
beforeEach(() => {
  window.localStorage.setItem('aeroagentsim.locale', 'en-US');
  edit.mockClear(); onProject.mockClear(); previewOperations.mockClear(); applyOperations.mockClear(); save.mockClear(); saveDraft.mockClear();
});
afterEach(cleanup);

it('prefills duration, step and seed in seconds and hides raw settings in Advanced (D5)', () => {
  render(element());
  expect(screen.getByLabelText('Duration (s)')).toHaveValue('300');
  expect(screen.getByLabelText('Step (s)')).toHaveValue('10');
  expect(screen.getByLabelText('Random seed')).toHaveValue('42');
  expect(screen.getByText('Advanced (raw tree)')).toBeTruthy();
  expect(screen.queryByRole('alert')).toBeNull();
  expect(screen.queryByText(/watermark|set_duration/)).toBeNull();
});

it('keeps duration Confirm after an identical parent rerender and marks an actual edit stale (P0)', async () => {
  const view = render(element());
  fireEvent.change(screen.getByLabelText('Duration (s)'), { target: { value: '480' } });
  fireEvent.click(within(durationForm()).getByRole('button', { name: 'Preview duration' }));
  await within(durationForm()).findByRole('button', { name: 'Confirm' });
  view.rerender(element({ draft: structuredClone(draft), project: { ...project } }));
  expect(within(durationForm()).getByRole('button', { name: 'Confirm' })).toBeEnabled();
  fireEvent.change(screen.getByLabelText('Duration (s)'), { target: { value: '481' } });
  expect(within(durationForm()).getByRole('status')).toHaveTextContent('Out of date');
  expect(within(durationForm()).queryByRole('button', { name: 'Confirm' })).toBeNull();
  fireEvent.change(screen.getByLabelText('Duration (s)'), { target: { value: '480' } });
  fireEvent.click(within(durationForm()).getByRole('button', { name: 'Confirm' }));
  await waitFor(() => expect(onProject).toHaveBeenCalled());
  expect(previewOperations.mock.calls[0][2][0]).toMatchObject({ op: 'set_duration', params: { seconds: 480 } });
  expect(applyOperations.mock.calls[0][2]).toEqual(previewOperations.mock.calls[0][2]);
  const updated = onProject.mock.calls[0][0];
  view.rerender(element({ project: updated, draft: updated.scenario }));
  expect(screen.getByLabelText('Duration (s)')).toHaveValue('480');
});

it('preserves fractional duration and step text while typing (T4-R2-003 numeric helper)', async () => {
  render(element());
  for (const label of ['Duration (s)', 'Step (s)']) for (const value of ['', '1', '1.', '1.5']) {
    fireEvent.change(screen.getByLabelText(label), { target: { value } });
    expect(screen.getByLabelText(label)).toHaveValue(value);
  }
  fireEvent.click(within(durationForm()).getByRole('button', { name: 'Preview duration' }));
  await within(durationForm()).findByRole('button', { name: 'Confirm' });
  expect(previewOperations.mock.calls[0][2][0].params.seconds).toBe(1.5);
});

it('reviews step and seed together and saves the confirmed server revision, retaining other settings', async () => {
  const view = render(element());
  fireEvent.change(screen.getByLabelText('Step (s)'), { target: { value: '2.5' } });
  fireEvent.change(screen.getByLabelText('Random seed'), { target: { value: '7' } });
  fireEvent.click(within(settingsForm()).getByRole('button', { name: 'Preview step & seed' }));
  await within(settingsForm()).findByRole('button', { name: 'Confirm' });
  expect(save).not.toHaveBeenCalled();
  view.rerender(element({ project: { ...project }, draft: structuredClone(draft) }));
  expect(within(settingsForm()).getByRole('button', { name: 'Confirm' })).toBeEnabled();
  fireEvent.click(within(settingsForm()).getByRole('button', { name: 'Confirm' }));
  await waitFor(() => expect(save).toHaveBeenCalledTimes(1));
  expect(save).toHaveBeenCalledWith('w1', { ...draft, run: { ...draft.run, advance_ns: 2_500_000_000, seed: 7 } }, 7);
  await waitFor(() => expect(onProject).toHaveBeenCalledWith(expect.objectContaining({ revision: 8 })));
  expect(edit).not.toHaveBeenCalled();
});

it('expires a step/seed review on a real value or base revision change', async () => {
  const view = render(element());
  fireEvent.click(within(settingsForm()).getByRole('button', { name: 'Preview step & seed' }));
  await within(settingsForm()).findByRole('button', { name: 'Confirm' });
  fireEvent.change(screen.getByLabelText('Random seed'), { target: { value: '43' } });
  expect(within(settingsForm()).getByRole('status')).toHaveTextContent('Out of date');
  fireEvent.change(screen.getByLabelText('Random seed'), { target: { value: '42' } });
  expect(within(settingsForm()).getByRole('button', { name: 'Confirm' })).toBeEnabled();
  view.rerender(element({ project: { ...project, revision: 8 } }));
  expect(within(settingsForm()).getByRole('status')).toHaveTextContent('Out of date');
  expect(within(settingsForm()).queryByRole('button', { name: 'Confirm' })).toBeNull();
});

it.each([['Duration (s)', '0'], ['Duration (s)', ''], ['Step (s)', 'abc'], ['Step (s)', '0'], ['Random seed', '1.5'], ['Random seed', '-1']])('rejects invalid %s without saving or defaulting', (label, value) => {
  render(element());
  fireEvent.change(screen.getByLabelText(label), { target: { value } });
  const form = label === 'Duration (s)' ? durationForm() : settingsForm();
  expect(within(form).getByRole('alert')).toBeTruthy();
  const preview = label === 'Duration (s)' ? 'Preview duration' : 'Preview step & seed';
  expect(within(form).getByRole('button', { name: preview })).toBeDisabled();
  expect(save).not.toHaveBeenCalled(); expect(previewOperations).not.toHaveBeenCalled();
});
