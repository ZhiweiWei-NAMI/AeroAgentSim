import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { MemoryRouter } from 'react-router-dom';
import { I18nProvider } from '../i18n/I18nProvider';
import { TemplateApi } from '../newcomers/api';
import Workbench from './Workbench';
import { WorkbenchApi, type Project, type RunRecord } from './api';
import type { Session } from '../assistant/api';

const mocks = vi.hoisted(() => ({ panelRender: vi.fn(), session: undefined as Session | undefined }));
vi.mock('../assistant/AssistantPanel', () => ({ AssistantPanel: () => { mocks.panelRender(); return <input aria-label="Search objects" />; } }));
vi.mock('../assistant/motion/NeuralGlyph', () => ({ NeuralGlyph: () => null }));
vi.mock('../studio/ScenePreview', () => ({ ScenePreview: () => null }));
vi.mock('./useRunSession', async original => ({ ...await original<object>(), useRunSession: () => undefined }));
vi.mock('../assistant/useAssistantMode', () => ({ useAssistantMode: () => ({
  api: {}, assistant: { session: mocks.session, plan: mocks.session?.plan, applied: mocks.session?.applied.at(-1), busy: false, send: vi.fn(), apply: vi.fn() },
  connection: { connection: {}, save: vi.fn(), test: vi.fn() }, stage: 'configured', board: true,
  lastSection: { current: 'run' }, setFocus: vi.fn(), setViewing: vi.fn(), closeBoard: vi.fn(), openBoard: vi.fn(), switchMode: vi.fn(), applyDone: vi.fn(),
}) }));

const project: Project = { id: 'project', name: 'Project', revision: 4, updated_at: '', dirty: false, scenario: { run: { seed: 43 } },
  latest_version: { version: 2, revision: 4, digest: 'v2', created_at: '', validation: { valid: true, revision: 4, errors: [] } } };
const run = { id: 'initialized-run', status: 'initialized', scenario: 'project', studio_workspace: 'project', workspace_version: 1 } as RunRecord;
beforeEach(() => {
  localStorage.clear(); sessionStorage.clear(); mocks.panelRender.mockClear();
  const plan = { version: 1, created_at: '', note: '', base: {}, base_label: 'Project', title: 'Plan', summary: '', operations: [], assumptions: [], open_questions: [],
    compile: { ok: true, errors: [], warnings: [] }, validation: { state: 'valid' }, expectations: [], diff_base: { added: [], changed: [], removed: [] } };
  mocks.session = { id: 'session', workspace_id: 'project', created_at: '', updated_at: '', title: 'Plan', busy: false, last_seq: 0,
    events: [], alternatives: [], plan_versions: [], plan, applied: [{ plan_version: 1, workspace_id: 'project', version: 1, revision: 2, applied_at: '', report: { created: [], changed: [], removed: [] } }] } as Session;
  vi.spyOn(WorkbenchApi.prototype, 'project').mockResolvedValue(project);
  vi.spyOn(WorkbenchApi.prototype, 'runs').mockResolvedValue([]);
  vi.spyOn(WorkbenchApi.prototype, 'initialize').mockResolvedValue(run);
  vi.spyOn(WorkbenchApi.prototype, 'speed').mockResolvedValue({ id: run.id, factor: 1 });
  vi.spyOn(TemplateApi.prototype, 'kpis').mockResolvedValue({ values: {}, series: [] } as never);
});
afterEach(() => { cleanup(); vi.useRealTimers(); vi.restoreAllMocks(); });
function setup() { return render(<I18nProvider><MemoryRouter initialEntries={['/w/project?panel=assistant']}><Workbench /></MemoryRouter></I18nProvider>); }

it('T2-R2-004: the assistant button initializes exactly its named v1 after Form saved v2', async () => {
  setup();
  const button = await screen.findByTestId('as-initialize');
  await waitFor(() => expect(button).toBeEnabled());
  expect(button).toHaveTextContent('Initialize version 1');
  expect(screen.getByTestId('wb-init')).toHaveTextContent('Initialize v2');
  fireEvent.click(button);
  await waitFor(() => expect(WorkbenchApi.prototype.initialize).toHaveBeenCalledWith('project', 1));
  expect(await screen.findByText(/Initialized version 1\. The world is held/)).toBeVisible();
});

it('the shell initializer names and requests the latest saved server version', async () => {
  setup();
  const button = await screen.findByTestId('wb-init');
  expect(button).toHaveTextContent('Initialize v2');
  fireEvent.click(button);
  await waitFor(() => expect(WorkbenchApi.prototype.initialize).toHaveBeenCalledWith('project', 2));
});

it.each(['keyboard', 'button'])('T4-07: Esc restores focus to the %s palette invoker, including after Tab', async invoker => {
  setup();
  await screen.findByTestId('wb-version-chip');
  const control = invoker === 'keyboard' ? screen.getByLabelText('Search objects') : screen.getByTitle('Search objects and actions');
  control.focus();
  if (invoker === 'keyboard') fireEvent.keyDown(control, { key: 'k', ctrlKey: true });
  else fireEvent.click(control);
  const dialog = screen.getByRole('dialog');
  expect(dialog).toContainElement(document.activeElement as HTMLElement);
  fireEvent.keyDown(within(dialog).getByRole('textbox'), { key: 'Tab', shiftKey: true });
  expect(dialog).toContainElement(document.activeElement as HTMLElement);
  fireEvent.keyDown(document.activeElement!, { key: 'Escape' });
  expect(screen.queryByRole('dialog')).toBeNull();
  expect(control).toHaveFocus();
});

it('the five-second records poll skips unchanged data and still renders actual record changes', async () => {
  vi.useFakeTimers();
  const record = { id: 'completed', status: 'completed', scenario: 'project', results_final: false } as RunRecord;
  vi.mocked(WorkbenchApi.prototype.runs).mockImplementation(async () => [{ ...record }]);
  await act(async () => { setup(); });
  expect(screen.getByTestId('wb-version-chip')).toHaveTextContent('v2');
  const count = mocks.panelRender.mock.calls.length;
  await act(async () => { await vi.advanceTimersByTimeAsync(5000); });
  expect(mocks.panelRender).toHaveBeenCalledTimes(count);
  record.results_final = true;
  await act(async () => { await vi.advanceTimersByTimeAsync(5000); });
  expect(mocks.panelRender.mock.calls.length).toBeGreaterThan(count);
});
