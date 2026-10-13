import { fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, expect, it, vi } from 'vitest';
import { I18nProvider } from '../../i18n/I18nProvider';
import { RunSection } from './RunSection';
import type { Operation, OperationPreview } from './operations';

const L = (en: string, _zh: string) => en;
const edit = vi.fn();
const onProject = vi.fn();
const saveDraft = vi.fn(async () => ({ id: 'w1', revision: 7, scenario: {} }));
const previewOperations = vi.fn(async (_api: unknown, _project: unknown, operations: Operation[]): Promise<OperationPreview> =>
  ({ diff: { added: operations.map(op => ({ label: op.op })), changed: [], removed: [] }, notes: [] }));
const applyOperations = vi.fn(async () => ({ id: 'w1', revision: 8, scenario: {} }));

vi.mock('./operations', async (importOriginal: () => Promise<typeof import('./operations')>) => {
  const actual = await importOriginal();
  return {
    ...actual,
    previewOperations: (api: unknown, project: { id: string; revision: number }, operations: Operation[]) => previewOperations(api, project, operations),
    applyOperations,
  };
});

const api = {} as never;
const props = { api, studioApi: {} as never, project: { id: 'w1', revision: 7 } as never, edit, focus: undefined, zh: false, locale: 'en-US' as never, L, issues: [], onProject, saveDraft, reportValidity: vi.fn(), replaceDraft: vi.fn(), draft: {
  run: { until_ns: 300_000_000_000, advance_ns: 10_000_000_000, seed: 42 },
  engines: { tasks: { plugin: 'lowalt_tasks', config: {} } },
} };

beforeEach(() => {
  window.localStorage.setItem('aeroagentsim.locale', 'en-US');
  edit.mockClear(); onProject.mockClear(); previewOperations.mockClear(); applyOperations.mockClear();
});

it('shows duration/step/seed in seconds with an Advanced disclosure for the raw tree', () => {
  render(<I18nProvider><RunSection {...props} /></I18nProvider>);
  expect(screen.getByLabelText('Duration (s)')).toHaveValue('300');
  expect(screen.getByLabelText('Step (s)')).toHaveValue('10');
  expect(screen.getByLabelText('Random seed')).toHaveValue('42');
  expect(screen.getByText('Advanced (raw tree)')).toBeTruthy();
  expect(screen.queryByLabelText('until_ns')).toBeNull();
});

it('applies duration through set_duration when the lowalt tasks engine is present', async () => {
  render(<I18nProvider><RunSection {...props} /></I18nProvider>);
  fireEvent.change(screen.getByLabelText('Duration (s)'), { target: { value: '480' } });
  fireEvent.click(screen.getByRole('button', { name: 'Set duration' }));
  await new Promise(resolve => setTimeout(resolve, 0));
  expect(previewOperations).toHaveBeenCalled();
  expect(previewOperations.mock.calls[0][2][0].op).toBe('set_duration');
  expect(previewOperations.mock.calls[0][2][0].params).toEqual({ seconds: 480 });
  fireEvent.click(screen.getByRole('button', { name: 'Confirm' }));
  await new Promise(resolve => setTimeout(resolve, 0));
  expect(applyOperations).toHaveBeenCalled();
  expect(onProject).toHaveBeenCalled();
});

it('writes step and seed into the run tree directly, visibly refusing invalid values', () => {
  render(<I18nProvider><RunSection {...props} /></I18nProvider>);
  fireEvent.change(screen.getByLabelText('Step (s)'), { target: { value: '2' } });
  fireEvent.click(screen.getByRole('button', { name: 'Apply step to draft' }));
  expect(edit).toHaveBeenCalledWith('/run/advance_ns', 2_000_000_000);
  fireEvent.change(screen.getByLabelText('Random seed'), { target: { value: '7' } });
  fireEvent.click(screen.getByRole('button', { name: 'Apply seed to draft' }));
  expect(edit).toHaveBeenCalledWith('/run/seed', 7);
  fireEvent.change(screen.getByLabelText('Random seed'), { target: { value: '1.5' } });
  expect(screen.getByText(/integer of 0 or more/)).toBeTruthy();
  expect(screen.queryByRole('button', { name: 'Apply seed to draft' })).toBeNull();
  fireEvent.change(screen.getByLabelText('Duration (s)'), { target: { value: '0' } });
  expect(screen.getByText(/above 0 s/)).toBeTruthy();
  expect(edit).toHaveBeenCalledTimes(2);
});
