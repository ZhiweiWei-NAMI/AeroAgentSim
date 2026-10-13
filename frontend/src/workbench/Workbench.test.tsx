import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { MemoryRouter, useLocation } from 'react-router-dom';
import { I18nProvider } from '../i18n/I18nProvider';
import { TemplateApi } from '../newcomers/api';
import Workbench from './Workbench';
import { WorkbenchApi, type Project, type RunRecord } from './api';
import type { ApplyRecord, Session } from '../assistant/api';
import { emptyCommit, fixtureHeader } from '../behaviours/extension-fixture';
import { TemporalFeedStore } from '../feeds/temporal-store';
import { PlaybackClock } from '../viewport/clock';
import type { RunSession } from './useRunSession';
import { Details } from '../console/Details';

const mocks = vi.hoisted(() => ({ panelRender: vi.fn(), apply: vi.fn(), readSession: vi.fn(), session: undefined as Session | undefined, runSession: undefined as RunSession | undefined }));
vi.mock('../assistant/AssistantPanel', () => ({ AssistantPanel: () => { mocks.panelRender(); return <input aria-label="Search objects" />; } }));
vi.mock('../assistant/motion/NeuralGlyph', () => ({ NeuralGlyph: () => null }));
vi.mock('../studio/ScenePreview', () => ({ ScenePreview: () => null }));
vi.mock('../viewport/NonspatialViews', () => ({ NonspatialViews: () => null }));
vi.mock('./useRunSession', async original => ({ ...await original<object>(), useRunSession: (...args: unknown[]) => { mocks.readSession(...args); return mocks.runSession; } }));
vi.mock('../assistant/useAssistantMode', () => ({ useAssistantMode: () => ({
  api: {}, assistant: { session: mocks.session, plan: mocks.session?.plan, applied: mocks.session?.applied.at(-1), busy: false, send: vi.fn(), apply: mocks.apply },
  connection: { connection: {}, save: vi.fn(), test: vi.fn() }, stage: mocks.session?.applied.length ? 'configured' : 'proposed', board: true,
  lastSection: { current: 'run' }, setFocus: vi.fn(), setViewing: vi.fn(), closeBoard: vi.fn(), openBoard: vi.fn(), switchMode: vi.fn(), applyDone: vi.fn(),
}) }));

const project: Project = { id: 'project', name: 'Project', revision: 4, updated_at: '', dirty: false, scenario: { run: { seed: 43 } },
  latest_version: { version: 2, revision: 4, digest: 'v2', created_at: '', validation: { valid: true, revision: 4, errors: [] } } };
const run = { id: 'initialized-run', status: 'initialized', scenario: 'project', studio_workspace: 'project', workspace_version: 1 } as RunRecord;
beforeEach(() => {
  localStorage.clear(); sessionStorage.clear(); mocks.panelRender.mockClear();
  mocks.apply.mockReset(); mocks.readSession.mockClear();
  mocks.runSession = undefined;
  const plan = { version: 1, created_at: '', note: '', base: {}, base_label: 'Project', title: 'Plan', summary: '', operations: [], assumptions: [], open_questions: [],
    compile: { ok: true, errors: [], warnings: [] }, validation: { state: 'valid' }, expectations: [], diff_base: { added: [], changed: [], removed: [] } };
  mocks.session = { id: 'session', workspace_id: 'project', created_at: '', updated_at: '', title: 'Plan', busy: false, last_seq: 0,
    events: [], alternatives: [], plan_versions: [], plan, applied: [{ plan_version: 1, workspace_id: 'project', version: 1, revision: 2, applied_at: '', report: { created: [], changed: [], removed: [] } }] } as Session;
  vi.spyOn(WorkbenchApi.prototype, 'project').mockResolvedValue(project);
  vi.spyOn(WorkbenchApi.prototype, 'runs').mockResolvedValue([]);
  vi.spyOn(WorkbenchApi.prototype, 'initialize').mockResolvedValue(run);
  vi.spyOn(WorkbenchApi.prototype, 'speed').mockResolvedValue({ id: run.id, factor: 1 });
  vi.spyOn(TemplateApi.prototype, 'kpis').mockResolvedValue({ run_id: run.id, final: {}, series: [] });
});
afterEach(() => { cleanup(); vi.useRealTimers(); vi.restoreAllMocks(); });
function LocationProbe() { const location = useLocation(); return <output data-testid="current-url">{location.pathname}{location.search}</output>; }
function setup(entry = '/w/project?panel=assistant') { return render(<I18nProvider><MemoryRouter initialEntries={[entry]}><Workbench /><LocationProbe /></MemoryRouter></I18nProvider>); }

it('T2-R2-004: the assistant button names and initializes the latest server v2 after a Form save', async () => {
  setup();
  const button = await screen.findByTestId('as-initialize');
  await waitFor(() => expect(button).toBeEnabled());
  expect(button).toHaveTextContent('Initialize version 2');
  expect(screen.getByTestId('wb-init')).toHaveTextContent('Initialize v2');
  fireEvent.click(button);
  await waitFor(() => expect(WorkbenchApi.prototype.initialize).toHaveBeenCalledWith('project', 2));
  expect(await screen.findByText(/Initialized version 2\. The world is held/)).toBeVisible();
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
  const control = invoker === 'keyboard' ? screen.getByTestId('workbench') : screen.getByTitle('Search objects and actions');
  if (invoker === 'keyboard') control.tabIndex = -1;
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

it.each(['before','after'])('T2-R4-001: fast Confirm refreshes the applied workspace and ignores an initial load arriving %s the authoritative refresh', async order => {
  let finishInitial!: (value: Project) => void;
  let finishRefresh!: (value: Project) => void;
  const stale = { ...project, latest_version: null, dirty: true, revision: 4 };
  const fresh = { ...project, revision: 6, latest_version: { ...project.latest_version!, version: 1, revision: 6 } };
  const record: ApplyRecord = { plan_version: 1, workspace_id: 'project', version: 1, revision: 6, applied_at: '', report: { created: [], changed: [], removed: [] } };
  vi.mocked(WorkbenchApi.prototype.project).mockReset()
    .mockImplementationOnce(() => new Promise<Project>(resolve => { finishInitial = resolve; }))
    .mockImplementationOnce(() => new Promise<Project>(resolve => { finishRefresh = resolve; }));
  mocks.session!.applied = [];
  mocks.session!.plan = {...mocks.session!.plan!,operations:[{id:'duration',op:'set_duration',params:{seconds:600}}],
    diff_base:{added:[],removed:[],changed:[{id:'run',kind:'scene',label:'Run',fields:[{name:'duration_s',before:300,after:600}]}]}};
  mocks.apply.mockImplementation(async () => { mocks.session = { ...mocks.session!, applied: [record] }; return record; });
  setup();
  fireEvent.click(await screen.findByTestId('as-confirm'));
  await waitFor(() => expect(WorkbenchApi.prototype.project).toHaveBeenCalledTimes(2));
  expect(WorkbenchApi.prototype.project).toHaveBeenNthCalledWith(2,record.workspace_id);
  if(order==='before')await act(async () => finishInitial(stale));
  await act(async () => finishRefresh(fresh));
  await waitFor(() => expect(screen.getByTestId('wb-version-chip')).toHaveTextContent('v1'));
  const initialize = screen.getByTestId('as-initialize');
  expect(initialize).toHaveTextContent('Initialize version 1'); expect(initialize).toBeEnabled();
  expect(WorkbenchApi.prototype.project).toHaveBeenCalledTimes(2);
  if(order==='after')await act(async () => finishInitial(stale));
  expect(screen.getByTestId('wb-version-chip')).toHaveTextContent('v1');
  expect(screen.getByTestId('wb-init')).toHaveTextContent('Initialize v1');
  fireEvent.click(initialize);
  await waitFor(() => expect(WorkbenchApi.prototype.initialize).toHaveBeenCalledWith('project', 1));
});

function recorded(mode: 'live' | 'replay' = 'replay') {
  const store = new TemporalFeedStore({ ...fixtureHeader, presentation: [], end: { ns: '14000000000', microstep: 0 } });
  [emptyCommit(1), emptyCommit(2, '7000000000'), emptyCommit(3, '14000000000')].forEach(commit => store.ingest(commit));
  const clock = new PlaybackClock('0', '14000000000'); clock.seek(clock.end); store.seek(clock.ns);
  const session: RunSession = {
    store, clock, status: mode === 'live' ? 'running' : 'completed', journalComplete: mode === 'replay', followingLive: mode === 'live', revision: 1,
    inputSource: { serialize: vi.fn() },
    seekOffset: vi.fn((offset: number) => {
      const commit = store.commits[offset]; if (!commit) return;
      clock.seek(commit.at.ns); session.cut = commit.commitIndex; session.followingLive = false; store.seek(clock.ns, session.cut);
    }),
    seekCut: vi.fn((index: number) => session.seekOffset(store.commits.findIndex(commit => commit.commitIndex === index))),
    togglePlayback: vi.fn(() => { if (clock.playing) clock.pause(); else clock.play(); session.cut = undefined; session.followingLive = false; }),
    backToLive: vi.fn(() => { clock.pause(); clock.seek(clock.end); session.cut = undefined; session.followingLive = mode === 'live'; store.seek(clock.ns); }),
    setSpeed: vi.fn(),
  };
  mocks.runSession = session;
  vi.mocked(WorkbenchApi.prototype.runs).mockResolvedValue([{ ...run, status: session.status, until_ns: '14000000000' }]);
  return session;
}

it.each(['live', 'replay'] as const)('T1-R4-004 / T3-R4-001: %s timeline persists nanoseconds and cut, restores the URL, and preserves selection', async mode => {
  const session = recorded(mode);
  const control = vi.spyOn(WorkbenchApi.prototype, 'control');
  const view = setup(`/w/project?run=${run.id}&mode=${mode}&sel=vehicle&panel=assistant`);
  fireEvent.change(await screen.findByLabelText('Recorded timeline'), { target: { value: '1' } });
  await waitFor(() => expect(screen.getByTestId('current-url')).toHaveTextContent('t=7000000000'));
  const url = screen.getByTestId('current-url').textContent!;
  expect(url).toContain('cut=2'); expect(url).toContain('sel=vehicle');
  expect(session.seekOffset).toHaveBeenCalledWith(1);
  view.unmount();
  setup(url);
  expect(mocks.readSession).toHaveBeenLastCalledWith(window.location.origin, run.id, mode, 2, '7000000000');
  fireEvent.click(await screen.findByTestId('wb-back-live'));
  await waitFor(() => expect(screen.getByTestId('current-url').textContent).not.toContain('cut='));
  const returned = new URL(screen.getByTestId('current-url').textContent!, 'http://api');
  expect(returned.searchParams.get('t')).toBe(mode === 'live' ? null : '14000000000');
  expect(returned.searchParams.get('sel')).toBe('vehicle');
  expect(control).not.toHaveBeenCalled(); expect(session.inputSource.serialize).not.toHaveBeenCalled();
});

it('T1-R4-004: a live URL without t keeps following the live head', async () => {
  const session = recorded('live'); setup(`/w/project?run=${run.id}&mode=live&panel=assistant`);
  await screen.findByTestId('wb-version-chip');
  expect(session.followingLive).toBe(true);
  expect(screen.getByTestId('current-url').textContent).not.toContain('t=');
  expect(mocks.readSession).toHaveBeenLastCalledWith(window.location.origin, run.id, 'live', undefined, undefined);
});

it('T1-R4-004: refresh during playback records the current nanosecond position', async () => {
  const session = recorded(); setup(`/w/project?run=${run.id}&mode=replay&panel=assistant`);
  fireEvent.click(await screen.findByTestId('wb-playback'));
  session.clock.seek('9000000001');
  fireEvent(window, new Event('beforeunload'));
  expect(screen.getByTestId('current-url')).toHaveTextContent('t=9000000001');
  expect(screen.getByTestId('current-url').textContent).not.toContain('cut=');
});

it('T4-R4-003: Space activates the focused Records button and leaves playback stopped', async () => {
  const session = recorded(); setup(`/w/project?run=${run.id}&mode=replay&panel=assistant`);
  const button = await screen.findByTestId('wb-records-toggle'); button.focus();
  await userEvent.setup().keyboard(' ');
  expect(screen.getByTestId('current-url')).toHaveTextContent('records=1');
  expect(session.togglePlayback).not.toHaveBeenCalled(); expect(session.clock.playing).toBe(false);
});

it.each(['button', 'range', 'checkbox', 'input', 'select', 'textarea', 'editable', 'role-button'])('T4-R4-003 / T4-R5-001: a %s owns its editing/activation keys while search stays global', async kind => {
  const session = recorded(); setup(`/w/project?run=${run.id}&mode=replay&panel=assistant`);
  await screen.findByTestId('wb-version-chip');
  const element = document.createElement(kind === 'editable' || kind === 'role-button' ? 'div' : kind === 'range' || kind === 'checkbox' ? 'input' : kind);
  if (kind === 'range' || kind === 'checkbox') (element as HTMLInputElement).type = kind;
  if (kind === 'editable') element.setAttribute('contenteditable', 'true');
  if (kind === 'role-button') element.setAttribute('role', 'button');
  document.body.append(element);
  const child = ['button', 'editable', 'role-button'].includes(kind) ? element.appendChild(document.createElement('span')) : element;
  try {
    const activationOnly = ['button', 'checkbox', 'role-button'].includes(kind);
    for (const key of activationOnly ? [' ', 'Enter'] : [' ', 'r', 'g', 'l']) fireEvent.keyDown(child, { key });
    expect(session.togglePlayback).not.toHaveBeenCalled(); expect(session.backToLive).not.toHaveBeenCalled();
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(screen.getByTestId('current-url').textContent).not.toContain('records=1');
    fireEvent.keyDown(child, { key: 'k', ctrlKey: true });
    expect(screen.getByRole('dialog')).toBeVisible();
    fireEvent.keyDown(within(screen.getByRole('dialog')).getByRole('textbox'), { key: 'Escape' });
    expect(screen.queryByRole('dialog')).toBeNull();
  } finally { element.remove(); }
});

it.each(['ctrlKey', 'metaKey'] as const)('T4-R5-001: %s+K and Escape keep working on the focused Records button', async modifier => {
  setup('/w/project?panel=assistant');
  const records = await screen.findByTestId('wb-records-toggle');
  await userEvent.setup().click(records);
  expect(records).toHaveFocus();
  expect(screen.getByTestId('current-url')).toHaveTextContent('records=1');
  fireEvent.keyDown(records, { key: 'k', [modifier]: true });
  const palette = screen.getByRole('dialog');
  fireEvent.keyDown(within(palette).getByRole('textbox'), { key: 'Escape' });
  expect(records).toHaveFocus();
  expect(screen.getByTestId('current-url')).toHaveTextContent('records=1');
  fireEvent.keyDown(records, { key: 'Escape' });
  expect(screen.getByTestId('current-url')).not.toHaveTextContent('records=1');
});

it('T4-R5-001: Escape closes the active details dialog before Records', async () => {
  setup('/w/project?panel=assistant');
  const records = await screen.findByTestId('wb-records-toggle');
  fireEvent.click(records);
  render(<I18nProvider><Details title="Receipt details"><p>Recorded receipt</p></Details></I18nProvider>);
  fireEvent.click(screen.getByTestId('details-trigger'));
  const dialog = screen.getByRole('dialog');
  fireEvent.keyDown(within(dialog).getByRole('button', { name: 'Close' }), { key: 'Escape' });
  expect(screen.queryByRole('dialog')).toBeNull();
  expect(screen.getByTestId('current-url')).toHaveTextContent('records=1');
  fireEvent.keyDown(records, { key: 'Escape' });
  expect(screen.getByTestId('current-url')).not.toHaveTextContent('records=1');
});

it('T4-R4-003: a local defaultPrevented event stays local, while Space on the observation surface controls playback', async () => {
  const session = recorded(); setup(`/w/project?run=${run.id}&mode=replay&panel=assistant`);
  await screen.findByTestId('wb-version-chip');
  const surface = document.createElement('div'); document.body.append(surface);
  const consume = (event: KeyboardEvent) => event.preventDefault(); surface.addEventListener('keydown', consume);
  try {
    fireEvent.keyDown(surface, { key: ' ' }); expect(session.togglePlayback).not.toHaveBeenCalled();
    surface.removeEventListener('keydown', consume);
    fireEvent.keyDown(surface, { key: ' ' }); expect(session.togglePlayback).toHaveBeenCalledOnce();
  } finally { surface.remove(); }
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

it.each(['en-US', 'zh-CN'])('T2-R3-007: history explains that the simulation keeps running in the active %s locale', async locale => {
  localStorage.setItem('aeroagentsim.locale', locale);
  const store = new TemporalFeedStore({ ...fixtureHeader, presentation: [] });
  store.ingest(emptyCommit(1)); store.ingest(emptyCommit(2, '93000000000')); store.seek('0', 1);
  const backToLive = vi.fn(), togglePlayback = vi.fn(), serialize = vi.fn();
  mocks.runSession = {
    store, clock: new PlaybackClock('0', '93000000000'), inputSource: { serialize }, status: 'running',
    followingLive: false, journalComplete: false, cut: 1, revision: 1,
    backToLive, togglePlayback, seekOffset: vi.fn(), seekCut: vi.fn(), setSpeed: vi.fn(),
  };
  vi.mocked(WorkbenchApi.prototype.runs).mockResolvedValue([{ ...run, status: 'running', until_ns: '360000000000' }]);
  const control = vi.spyOn(WorkbenchApi.prototype, 'control').mockResolvedValue({ id: run.id, requested: 'pause', status: 'paused' });
  setup(`/w/project?run=${run.id}&mode=live&panel=assistant`);
  const notice = await screen.findByTestId('wb-history');
  const english = 'Viewing history — the simulation keeps running.';
  const chinese = '正在查看历史——仿真仍在运行。';
  expect(notice).toHaveTextContent(locale === 'en-US' ? `${english} Back to live (L)` : `${chinese} 返回实时 (L)`);
  expect(notice).not.toHaveTextContent(locale === 'en-US' ? chinese : english);
  expect(await screen.findByTestId('wb-pause')).toBeEnabled();
  fireEvent.click(screen.getByTestId('wb-playback')); fireEvent.click(screen.getByTestId('wb-playback'));
  fireEvent.click(screen.getByTestId('wb-back-live'));
  fireEvent.keyDown(document.body, { key: 'l' });
  expect(togglePlayback).toHaveBeenCalledTimes(2); expect(backToLive).toHaveBeenCalledTimes(2);
  expect(serialize).not.toHaveBeenCalled(); expect(control).not.toHaveBeenCalled();
});
