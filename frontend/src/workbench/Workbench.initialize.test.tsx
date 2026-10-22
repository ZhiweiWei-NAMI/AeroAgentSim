import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { MemoryRouter, useLocation } from 'react-router-dom';
import { I18nProvider } from '../i18n/I18nProvider';
import { TemplateApi } from '../newcomers/api';
import Workbench, { initializationOutcome } from './Workbench';
import { WorkbenchApi, type Project, type RunRecord } from './api';
import type { RunSession } from './useRunSession';

const mocks = vi.hoisted(() => ({ runSession: undefined as RunSession | undefined }));
vi.mock('../assistant/AssistantPanel', () => ({ AssistantPanel: () => null }));
vi.mock('../assistant/motion/NeuralGlyph', () => ({ NeuralGlyph: () => null }));
vi.mock('../studio/ScenePreview', () => ({ ScenePreview: () => null }));
vi.mock('../viewport/NonspatialViews', () => ({ NonspatialViews: () => null }));
vi.mock('./useRunSession', async original => ({ ...await original<object>(), useRunSession: () => mocks.runSession }));
vi.mock('../assistant/useAssistantMode', () => ({ useAssistantMode: () => ({
  api: {}, assistant: { session: undefined, plan: undefined, applied: [], busy: false, send: vi.fn(), apply: vi.fn() },
  connection: { connection: {}, save: vi.fn(), test: vi.fn() }, stage: 'proposed', board: true,
  lastSection: { current: 'run' }, setFocus: vi.fn(), setViewing: vi.fn(), closeBoard: vi.fn(), openBoard: vi.fn(), switchMode: vi.fn(), applyDone: vi.fn(),
}) }));

const project: Project = { id: 'project', name: 'Project', revision: 4, updated_at: '', dirty: false, scenario: { run: { seed: 43 } },
  latest_version: { version: 2, revision: 4, digest: 'v2', created_at: '', validation: { valid: true, revision: 4, errors: [] } } };
const initialized = { id: 'initialized-run', status: 'initialized', scenario: 'project', studio_workspace: 'project', workspace_version: 2 } as RunRecord;

beforeEach(() => {
  vi.stubGlobal('ResizeObserver', class { observe() {} disconnect() {} });
  localStorage.clear(); sessionStorage.clear();
  mocks.runSession = undefined;
  vi.spyOn(WorkbenchApi.prototype, 'project').mockResolvedValue(project);
  vi.spyOn(WorkbenchApi.prototype, 'runs').mockResolvedValue([]);
  vi.spyOn(WorkbenchApi.prototype, 'initialize').mockResolvedValue(initialized);
  vi.spyOn(WorkbenchApi.prototype, 'speed').mockResolvedValue({ id: initialized.id, factor: 1 });
  vi.spyOn(WorkbenchApi.prototype, 'setSpeed').mockResolvedValue({ id: initialized.id, factor: 5 });
  vi.spyOn(TemplateApi.prototype, 'kpis').mockResolvedValue({ run_id: initialized.id, final: {}, series: [] });
});
afterEach(() => { cleanup(); vi.useRealTimers(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });
function LocationProbe() { const location = useLocation(); return <output data-testid="current-url">{location.pathname}{location.search}</output>; }
function setup(entry = '/w/project') { return render(<I18nProvider><MemoryRouter initialEntries={[entry]}><Workbench /><LocationProbe /></MemoryRouter></I18nProvider>); }

it('treats only a complete initialized receipt as success', () => {
  expect(initializationOutcome({ id: 'r', status: 'initialized' }).state).toBe('initialized');
  expect(initializationOutcome({ id: 'r', status: 'faulted' }).state).toBe('failed');
  expect(initializationOutcome({ id: 'r', status: 'interrupted' }).state).toBe('failed');
  expect(initializationOutcome({ id: 'r', status: 'created' }).state).toBe('pending');
  expect(initializationOutcome({ id: 'r', status: 'teleported' }).state).toBe('malformed');
  expect(initializationOutcome({ status: 'initialized' }).state).toBe('malformed');
  expect(initializationOutcome(null).state).toBe('malformed');
});

it('shows the t = 0 success and Start only for an acknowledged initialized run', async () => {
  let runs: RunRecord[] = [];
  vi.mocked(WorkbenchApi.prototype.initialize).mockImplementation(async () => { runs = [initialized]; return initialized; });
  vi.mocked(WorkbenchApi.prototype.runs).mockImplementation(async () => runs);
  setup();
  fireEvent.click(await screen.findByTestId('wb-init'));
  expect(await screen.findByText(/Initialized version 2\. The world is held at t = 0/)).toBeVisible();
  expect(await screen.findByTestId('wb-start')).toBeEnabled();
  await waitFor(() => expect(screen.getByTestId('current-url')).toHaveTextContent('run=initialized-run'));
  expect(screen.getByTestId('current-url')).toHaveTextContent('mode=live');
});

it.each(['en-US', 'zh-CN'])('keeps a faulted initialization inspectable and never offers Start (%s)', async locale => {
  localStorage.setItem('aeroagentsim.locale', locale);
  const zh = locale === 'zh-CN';
  const faulted = { id: 'faulted-run', status: 'faulted', scenario: 'project', studio_workspace: 'project', workspace_version: 2, error: 'worker stopped' } as RunRecord;
  let runs: RunRecord[] = [];
  vi.mocked(WorkbenchApi.prototype.initialize).mockImplementation(async () => { runs = [faulted]; return faulted; });
  vi.mocked(WorkbenchApi.prototype.runs).mockImplementation(async () => runs);
  setup();
  fireEvent.click(await screen.findByTestId('wb-init'));
  const notice = await screen.findByTestId('wb-init-failed');
  expect(notice).toHaveTextContent(zh ? '初始化失败' : 'Initialization failed');
  expect(notice).toHaveTextContent('faulted-run');
  expect(notice).toHaveTextContent('worker stopped');
  expect(screen.queryByText(/Initialized version 2|已初始化版本 2/)).toBeNull();
  expect(screen.queryByTestId('wb-start')).toBeNull();
  await waitFor(() => expect(screen.getByTestId('current-url')).toHaveTextContent('run=faulted-run'));
  expect(screen.getByTestId('current-url')).toHaveTextContent('mode=replay');
  expect(WorkbenchApi.prototype.setSpeed).not.toHaveBeenCalled();
});

it.each(['en-US', 'zh-CN'])('reports a pending initialization without success or Start (%s)', async locale => {
  localStorage.setItem('aeroagentsim.locale', locale);
  const zh = locale === 'zh-CN';
  const pendingRun = { id: 'pending-run', status: 'created', scenario: 'project', studio_workspace: 'project', workspace_version: 2 } as RunRecord;
  let runs: RunRecord[] = [];
  vi.mocked(WorkbenchApi.prototype.initialize).mockImplementation(async () => { runs = [pendingRun]; return pendingRun; });
  vi.mocked(WorkbenchApi.prototype.runs).mockImplementation(async () => runs);
  setup();
  fireEvent.click(await screen.findByTestId('wb-init'));
  const notice = await screen.findByTestId('wb-init-pending');
  expect(notice).toHaveTextContent(zh ? '初始化仍在等待中' : 'Initialization is still pending');
  expect(notice).toHaveTextContent('pending-run');
  expect(screen.queryByText(/Initialized version 2|已初始化版本 2/)).toBeNull();
  expect(screen.queryByTestId('wb-start')).toBeNull();
  await waitFor(() => expect(screen.getByTestId('current-url')).toHaveTextContent('run=pending-run'));
  expect(screen.getByTestId('current-url')).toHaveTextContent('mode=live');
  expect(WorkbenchApi.prototype.setSpeed).not.toHaveBeenCalled();
});

it('rejects a malformed initialization response without pretending a run exists', async () => {
  vi.mocked(WorkbenchApi.prototype.initialize).mockResolvedValue({} as RunRecord);
  setup();
  fireEvent.click(await screen.findByTestId('wb-init'));
  const notice = await screen.findByTestId('wb-init-malformed');
  expect(notice).toHaveTextContent('malformed');
  expect(screen.queryByText(/Initialized version 2/)).toBeNull();
  expect(screen.queryByTestId('wb-start')).toBeNull();
  expect(screen.getByTestId('current-url')).not.toHaveTextContent('run=');
  expect(WorkbenchApi.prototype.speed).not.toHaveBeenCalled();
});

it('names an unreadable run so it stays findable in Records, in Chinese too', async () => {
  localStorage.setItem('aeroagentsim.locale', 'zh-CN');
  vi.mocked(WorkbenchApi.prototype.initialize).mockResolvedValue({ id: 'mystery-run', status: 'teleported' } as unknown as RunRecord);
  setup();
  fireEvent.click(await screen.findByTestId('wb-init'));
  const notice = await screen.findByTestId('wb-init-malformed');
  expect(notice).toHaveTextContent('格式异常');
  expect(notice).toHaveTextContent('mystery-run');
  expect(screen.getByTestId('current-url')).not.toHaveTextContent('run=');
});
