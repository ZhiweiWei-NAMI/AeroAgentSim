import { useState } from 'react';
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { I18nProvider, useI18n } from '../../../i18n/I18nProvider';
import { ApiError, type Project, type WorkbenchApi } from '../../api';
import { operation, type OperationPreview } from '../operations';
import { OpApply, StagedNumber, parseStagedNumber } from './op-ui';

const base = { id: 'w1', revision: 7, scenario: {} } as Project;
const data: OperationPreview = { diff: { added: [], changed: [{ kind: 'scene', id: 'duration_s', label: 'Duration', label_zh: '时长', fields: [{ name: 'duration_s', before: 2, after: 120 }] }], removed: [] }, notes: [] };
const request = vi.fn(), loadProject = vi.fn(), onProject = vi.fn();
const api = { request, project: loadProject } as unknown as WorkbenchApi;
const saveDraft = vi.fn(async () => base);

beforeEach(() => {
  window.localStorage.setItem('aeroagentsim.locale', 'en-US');
  request.mockReset().mockImplementation(async (_path: string, init: { body: { dry_run?: boolean } }) => init.body.dry_run ? data : { ...base, revision: 8 });
  loadProject.mockReset().mockResolvedValue({ ...base, revision: 8 });
  onProject.mockClear(); saveDraft.mockReset().mockResolvedValue(base);
});
afterEach(cleanup);

function Harness({ seconds = 120, revision = 7, missing }: { seconds?: number; revision?: number; missing?: string }) {
  return <OpApply api={api} project={{ ...base, revision }} onProject={onProject} saveDraft={saveDraft}
    operations={[operation('set_duration', { seconds })]} label="Preview" missing={missing} />;
}

it('keeps Confirm after a parent poll and applies the reviewed ID (all four P0 preview findings)', async () => {
  const view = render(<I18nProvider><Harness /></I18nProvider>);
  fireEvent.click(screen.getByRole('button', { name: 'Preview' }));
  await screen.findByRole('button', { name: 'Confirm' });
  const reviewed = request.mock.calls[0][1].body.operations;
  view.rerender(<I18nProvider><Harness /></I18nProvider>);
  view.rerender(<I18nProvider><Harness /></I18nProvider>);
  expect(screen.getByRole('button', { name: 'Confirm' })).toBeEnabled();
  expect(screen.queryByText(/Out of date/)).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: 'Confirm' }));
  await waitFor(() => expect(onProject).toHaveBeenCalledWith(expect.objectContaining({ revision: 8 })));
  expect(request.mock.calls[1][1].body.operations).toEqual(reviewed);
  expect(request.mock.calls[1][1].body.revision).toBe(7);
});

it('removes Confirm when one actual parameter changes, and restores it when restored exactly', async () => {
  const view = render(<I18nProvider><Harness /></I18nProvider>);
  fireEvent.click(screen.getByRole('button', { name: 'Preview' }));
  await screen.findByRole('button', { name: 'Confirm' });
  view.rerender(<I18nProvider><Harness seconds={121} /></I18nProvider>);
  expect(screen.getByRole('status')).toHaveTextContent('Out of date');
  expect(screen.queryByRole('button', { name: 'Confirm' })).toBeNull();
  view.rerender(<I18nProvider><Harness /></I18nProvider>);
  expect(screen.getByRole('button', { name: 'Confirm' })).toBeEnabled();
});

it('requires a new preview after the base revision changes', async () => {
  const view = render(<I18nProvider><Harness /></I18nProvider>);
  fireEvent.click(screen.getByRole('button', { name: 'Preview' }));
  await screen.findByRole('button', { name: 'Confirm' });
  view.rerender(<I18nProvider><Harness revision={8} /></I18nProvider>);
  expect(screen.getByRole('status')).toHaveTextContent('Out of date');
  expect(screen.queryByRole('button', { name: 'Confirm' })).toBeNull();
});

it('cannot re-preview invalid inputs or confirm after a prerequisite disappears', async () => {
  const view = render(<I18nProvider><Harness /></I18nProvider>);
  fireEvent.click(screen.getByRole('button', { name: 'Preview' }));
  await screen.findByRole('button', { name: 'Confirm' });
  view.rerender(<I18nProvider><Harness missing="Pick a place." /></I18nProvider>);
  expect(screen.getByRole('button', { name: 'Preview again' })).toBeDisabled();
  expect(screen.queryByRole('button', { name: 'Confirm' })).toBeNull();
  expect(screen.getByRole('note')).toHaveTextContent('Pick a place.');
  expect(request).toHaveBeenCalledTimes(1);
});

it('selects Chinese backend notes, gust labels and rejection messages (T4-R2-014)', async () => {
  window.localStorage.setItem('aeroagentsim.locale', 'zh-CN');
  request.mockResolvedValueOnce({ diff: { added: [{ kind: 'event', id: 'wind-gust-1', label: 'Wind gust: 9 m/s' }], changed: [], removed: [] }, notes: ['Kitchen k has no meal orders / 厨房 k 没有餐食订单'] });
  render(<I18nProvider><Harness /></I18nProvider>);
  fireEvent.click(screen.getByRole('button', { name: 'Preview' }));
  await screen.findByRole('button', { name: '确认' });
  expect(screen.getByText('阵风：9 m/s')).toBeTruthy();
  expect(screen.getByText('厨房 k 没有餐食订单')).toBeTruthy();
  expect(screen.queryByText(/Kitchen/)).toBeNull();
  request.mockRejectedValueOnce(new ApiError(422, JSON.stringify({ field: 'seconds', message: 'Invalid duration / 时长无效' })));
  fireEvent.click(screen.getByRole('button', { name: '确认' }));
  await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('时长无效'));
  expect(screen.getByRole('alert')).not.toHaveTextContent('Invalid duration');
  expect(onProject).not.toHaveBeenCalled();
});

function NumberHarness() {
  const [text, setText] = useState('');
  return <StagedNumber label="Amount" value={text} min={0} onChange={setText} />;
}

it('hides untouched errors and preserves intermediate decimal text (T4-R2-003 / D5)', () => {
  render(<I18nProvider><NumberHarness /></I18nProvider>);
  expect(screen.queryByRole('alert')).toBeNull();
  for (const value of ['1', '1.', '1.5']) {
    fireEvent.change(screen.getByLabelText('Amount'), { target: { value } });
    expect(screen.getByLabelText('Amount')).toHaveValue(value);
  }
  expect(parseStagedNumber('1.5')).toBe(1.5);
  fireEvent.change(screen.getByLabelText('Amount'), { target: { value: '' } });
  fireEvent.blur(screen.getByLabelText('Amount'));
  expect(screen.getByRole('alert')).toHaveTextContent('Required.');
});

it.each(['', ' ', 'NaN', 'Infinity', '0x10', '-', '1e', '1.2.3'])('does not turn invalid text "%s" into a number', text => {
  expect(parseStagedNumber(text)).toBeUndefined();
});

it('updates a shown numeric error when the locale changes', () => {
  function LocalizedNumber() {
    const { setLocale } = useI18n();
    return <><NumberHarness /><button onClick={() => setLocale('zh-CN')}>Chinese</button></>;
  }
  render(<I18nProvider><LocalizedNumber /></I18nProvider>);
  fireEvent.change(screen.getByLabelText('Amount'), { target: { value: '-1' } });
  expect(screen.getByRole('alert')).toHaveTextContent('Cannot be below 0.');
  fireEvent.click(screen.getByRole('button', { name: 'Chinese' }));
  expect(screen.getByRole('alert')).toHaveTextContent('不能小于 0。');
  expect(screen.getByRole('alert')).not.toHaveTextContent('Cannot');
});

it('blocks sibling Preview/Confirm until the authoritative GET completes, then expires reviews and previews the new revision (T2-R3-006 / T3-R3-002 / T4-R3-004)', async () => {
  let finishRead!: (project: Project) => void;
  loadProject.mockImplementationOnce(() => new Promise<Project>(resolve => { finishRead = resolve; }));
  const form = (name: string, seconds: number) => <fieldset aria-label={name}><Harness seconds={seconds} /></fieldset>;
  render(<I18nProvider>{form('First', 120)}{form('Reviewed sibling', 180)}{form('Unreviewed sibling', 240)}</I18nProvider>);
  const first = within(screen.getByRole('group', { name: 'First' }));
  const reviewed = within(screen.getByRole('group', { name: 'Reviewed sibling' }));
  const unreviewed = within(screen.getByRole('group', { name: 'Unreviewed sibling' }));
  fireEvent.click(first.getByRole('button', { name: 'Preview' }));
  await first.findByRole('button', { name: 'Confirm' });
  fireEvent.click(reviewed.getByRole('button', { name: 'Preview' }));
  await reviewed.findByRole('button', { name: 'Confirm' });
  fireEvent.click(first.getByRole('button', { name: 'Confirm' }));
  await waitFor(() => expect(loadProject).toHaveBeenCalledOnce());
  expect(reviewed.getByRole('button', { name: 'Confirm' })).toBeDisabled();
  expect(unreviewed.getByRole('button', { name: 'Preview' })).toBeDisabled();
  expect(reviewed.getByRole('note')).toHaveTextContent('Applying another change…');
  expect(unreviewed.getByRole('note')).toHaveTextContent('Applying another change…');
  fireEvent.click(unreviewed.getByRole('button', { name: 'Preview' }));
  expect(request).toHaveBeenCalledTimes(3);
  await act(async () => finishRead({ ...base, revision: 8 }));
  await waitFor(() => expect(onProject).toHaveBeenCalledOnce());
  expect(reviewed.queryByRole('button', { name: 'Confirm' })).toBeNull();
  expect(reviewed.getByRole('status')).toHaveTextContent('Out of date');
  fireEvent.click(reviewed.getByRole('button', { name: 'Preview again' }));
  await reviewed.findByRole('button', { name: 'Confirm' });
  expect(request.mock.calls[3][1].body.revision).toBe(8);
  fireEvent.click(unreviewed.getByRole('button', { name: 'Preview' }));
  await unreviewed.findByRole('button', { name: 'Confirm' });
  expect(request.mock.calls[4][1].body.revision).toBe(8);
  expect(screen.queryByRole('alert')).toBeNull();
  expect(screen.queryByText(/changed elsewhere/)).toBeNull();
});

it('aligns the operation actions with the input baseline inside a form grid (D13)', () => {
  const { container } = render(<I18nProvider><div className="wb-form-grid"><Harness /></div></I18nProvider>);
  expect(container.querySelector('.wb-form-grid > .wb-op-apply')).toHaveStyle({ alignSelf: 'end' });
});
