import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { I18nProvider } from './I18nProvider';
import { localized, translate } from './translate';
import { ConfigPanel } from '../workbench/ConfigPanel';
import { Gallery } from '../workbench/Gallery';
import Workbench from '../workbench/Workbench';
import { WorkbenchApi, type Project, type RunRecord } from '../workbench/api';
import { ExperimentPanel } from '../experiments/ExperimentPanel';
import type { ExperimentReport } from '../experiments/api';
import { CompareRuns } from '../newcomers/CompareRuns';
import { TemplateApi, type ScenarioTemplate } from '../newcomers/api';
import { AssistantPanel } from '../assistant/AssistantPanel';
import type { Connection, Session } from '../assistant/api';
import type { AssistantSession } from '../assistant/useAssistant';
import { emptyCommit, fixtureHeader } from '../behaviours/extension-fixture';
import { TemporalFeedStore } from '../feeds/temporal-store';
import { PlaybackClock } from '../viewport/clock';
import type { RunSession } from '../workbench/useRunSession';

const mocks = vi.hoisted(() => ({ runSession: undefined as unknown }));

vi.mock('../workbench/useRunSession', async original => ({ ...await original<object>(), useRunSession: () => mocks.runSession }));
vi.mock('../assistant/useAssistantMode', () => ({ useAssistantMode: () => ({
  api: {}, assistant: { session: undefined, plan: undefined, applied: undefined, busy: false, send: vi.fn(), apply: vi.fn() },
  connection: { connection: undefined, error: undefined, saving: false, save: vi.fn(), test: vi.fn() }, error: undefined, stage: 'proposed', board: false,
  lastSection: { current: 'run' }, setFocus: vi.fn(), setViewing: vi.fn(), closeBoard: vi.fn(), openBoard: vi.fn(),
  switchMode: vi.fn(), applyDone: vi.fn(), retrySession: vi.fn(), overlay: undefined, previewWorkspace: undefined,
  morph: undefined, viewing: undefined, story: undefined, storyError: undefined,
}) }));
vi.mock('../assistant/motion/NeuralGlyph', () => ({ NeuralGlyph: () => null }));
vi.mock('../studio/ScenePreview', () => ({ ScenePreview: () => null }));
vi.mock('../viewport/NonspatialViews', () => ({ NonspatialViews: () => null }));
vi.mock('../viewport/ViewportView', () => ({ ViewportView: () => null }));

const LOCALES = ['en-US', 'zh-CN'] as const;
const zh = (locale: string) => locale === 'zh-CN';

const template: ScenarioTemplate = {
  format: 'aeroagentsim.template/v1', id: 'food-delivery', title: 'Meal delivery', title_zh: '外卖配送',
  summary: 'Meals reach customers.', summary_zh: '餐食送达客户。', category: 'logistics', difficulty: 'beginner', duration_s: 300,
  scenario: 'food-delivery.yaml', observe: ['Watch the deliveries.'], observe_zh: ['观察配送。'], try: ['Change the fleet size.'], try_zh: ['调整机队规模。'],
  knobs: [
    { id: 'fleet_size', label: 'Fleet size', label_zh: '机队规模', type: 'integer', default: 4, min: 1, max: 20, pointer: '/fleet_size' },
    { id: 'mode', label: 'Delivery mode', label_zh: '配送方式', type: 'choice', default: 'normal', options: ['normal', 'rush'], options_zh: ['普通', '加急'], pointer: '/mode' },
  ],
  kpis: ['tasks_completed'],
};

const project: Project = {
  id: 'project', name: 'Meal delivery project', revision: 3, updated_at: '', dirty: false,
  scenario: { fleet_size: 4, mode: 'normal', run: { seed: 7 } },
  template: { id: 'food-delivery', knobs: { fleet_size: 4, mode: 'normal' } },
  validation: { valid: true, revision: 3, errors: [] },
  latest_version: { version: 2, revision: 2, digest: 'v2', created_at: '', validation: { valid: true, revision: 2, errors: [] } },
};

const externalTemplate: ScenarioTemplate = {
  format: 'aeroagentsim.template/v1', id: 'external-patrol', title: 'External patrol',
  summary: 'A perimeter declared by an installed contribution.', category: 'security', difficulty: 'intermediate', duration_s: 600,
  scenario: 'external.yaml', observe: ['Watch the perimeter.'], try: ['Change the patrol altitude.'],
  knobs: [{ id: 'patrol_altitude_m', label: 'Patrol altitude (m)', type: 'number', default: 80, pointer: '/patrol/altitude_m' }],
  kpis: ['distance_flown_km'],
};

const run: RunRecord = { id: 'run-1', status: 'completed', scenario: 'project', studio_workspace: 'project', workspace_version: 2, until_ns: '14000000000' };
const faulted: RunRecord = { id: 'faulted-run', status: 'faulted', scenario: 'project', studio_workspace: 'project', workspace_version: 2, until_ns: '14000000000', error: 'worker exited before acknowledgement' };

function installLocale(locale: string) {
  localStorage.setItem('aeroagentsim.locale', locale);
}

function action(testId: string) {
  return screen.getByTestId(testId);
}

function expectAction(testId: string, english: string, chinese: string, locale: string, enabled = true) {
  const button = action(testId);
  expect(button).toHaveAccessibleName(zh(locale) ? chinese : english);
  expect(button).toHaveProperty('disabled', !enabled);
}

/** Locale-independent structure: the same buttons and states must exist in both languages. */
function stateSnapshot() {
  return {
    buttons: screen.queryAllByRole('button').map(button => ({ testId: button.getAttribute('data-testid'), disabled: button.hasAttribute('disabled') })),
    alerts: screen.queryAllByRole('alert').length,
    statuses: screen.queryAllByRole('status').length,
  };
}

function configApi(): WorkbenchApi {
  return { base: '/api' } as WorkbenchApi;
}

function renderConfig(locale: string, supplied: Project = project, section = 'controls') {
  installLocale(locale);
  return render(<I18nProvider><ConfigPanel api={configApi()} project={supplied} section={section} onSection={vi.fn()}
    onProject={vi.fn()} onInitialize={vi.fn()} initializing={false} onClose={vi.fn()} /></I18nProvider>);
}

function renderWorkbench(locale: string, path: string) {
  installLocale(locale);
  return render(<I18nProvider><MemoryRouter initialEntries={[path]}><Workbench /></MemoryRouter></I18nProvider>);
}

function recordedSession(): RunSession {
  const store = new TemporalFeedStore({ ...fixtureHeader, presentation: [], end: { ns: '14000000000', microstep: 0 } });
  [emptyCommit(1), emptyCommit(2, '7000000000'), emptyCommit(3, '14000000000')].forEach(commit => store.ingest(commit));
  const clock = new PlaybackClock('0', '14000000000'); clock.seek('7000000000'); store.seek(clock.ns);
  const session: RunSession = {
    store, clock, status: 'completed', journalComplete: true, followingLive: false, revision: 1, cut: 2,
    inputSource: { serialize: vi.fn() },
    seekOffset: vi.fn((offset: number) => { const commit = store.commits[offset]; if (commit) { clock.seek(commit.at.ns); store.seek(clock.ns, commit.commitIndex); } }),
    seekCut: vi.fn(), togglePlayback: vi.fn(), backToLive: vi.fn(), setSpeed: vi.fn(),
  };
  mocks.runSession = session;
  return session;
}

beforeEach(() => {
  localStorage.clear(); sessionStorage.clear();
  vi.stubGlobal('ResizeObserver', class { observe() {} disconnect() {} });
  vi.spyOn(WorkbenchApi.prototype, 'project').mockResolvedValue(project);
  vi.spyOn(WorkbenchApi.prototype, 'runs').mockResolvedValue([]);
  vi.spyOn(WorkbenchApi.prototype, 'initialize').mockResolvedValue(run);
  vi.spyOn(TemplateApi.prototype, 'kpis').mockResolvedValue({ run_id: run.id, final: {}, series: [] });
  vi.spyOn(TemplateApi.prototype, 'template').mockResolvedValue(template);
  vi.spyOn(TemplateApi.prototype, 'algorithms').mockResolvedValue([]);
});

afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); localStorage.clear(); sessionStorage.clear(); });

it('configures and initializes the same controls in English and Chinese', async () => {
  const snapshots: Record<string, ReturnType<typeof stateSnapshot>> = {};
  for (const locale of LOCALES) {
    renderConfig(locale);
    expect(await screen.findByRole('spinbutton', { name: zh(locale) ? '机队规模' : 'Fleet size' })).toBeEnabled();
    expectAction('wb-save-version', 'Save version', '保存版本', locale, true);
    expectAction('wb-initialize', 'Initialize v2', '初始化 v2', locale, true);
    expect(action('wb-validate')).toHaveAccessibleName(zh(locale) ? '校验' : 'Validate');
    expect(action('wb-save-version')).toBeEnabled();
    expect(screen.getByTestId('wb-config')).toHaveAttribute('aria-label', zh(locale) ? '配置场景' : 'Configure scenario');
    snapshots[locale] = stateSnapshot();
    cleanup();
  }
  expect(snapshots['zh-CN']).toEqual(snapshots['en-US']);
});

it('keeps confirmation, review and creation identical in both locales', async () => {
  const report: ExperimentReport = {
    id: 'exp-1', contract: 'aeroagentsim.experiment/v1', template_id: 'food-delivery', template_title: 'Meal delivery', template_title_zh: '外卖配送',
    status: 'created', seeds: 3, jobs: 1, horizon_s: null, knobs: {}, vary: { fleet_size: [4, 8] }, total: 6, completed: 0, failed: 0, error: null,
    runs: [], table: [
      { config_id: 'config-0', knobs: { fleet_size: 4 }, metrics: {} },
      { config_id: 'config-1', knobs: { fleet_size: 8 }, metrics: {} },
    ],
  };
  const snapshots: Record<string, ReturnType<typeof stateSnapshot>> = {};
  for (const locale of LOCALES) {
    const api = { templates: vi.fn().mockResolvedValue([template]), request: vi.fn().mockResolvedValue(report) } as unknown as WorkbenchApi;
    installLocale(locale);
    render(<I18nProvider><ExperimentPanel api={api} onExperimentId={vi.fn()} onOpen={vi.fn()} onCompare={vi.fn()} /></I18nProvider>);
    await waitFor(() => expect(screen.getByTestId('experiment-template')).toHaveValue('food-delivery'));
    expect(screen.getByRole('heading', { name: zh(locale) ? '实验' : 'Experiments' })).toBeVisible();
    expect(screen.getByRole('combobox', { name: zh(locale) ? '模板' : 'Template' })).toHaveValue('food-delivery');
    fireEvent.click(screen.getByRole('checkbox', { name: zh(locale) ? '变化“机队规模”' : 'Vary Fleet size' }));
    fireEvent.change(screen.getByTestId('experiment-values-fleet_size'), { target: { value: '4, 8' } });
    await waitFor(() => expect(screen.getByTestId('experiment-review')).toBeEnabled());
    fireEvent.click(screen.getByTestId('experiment-review'));
    expect(screen.getByTestId('experiment-review-panel')).toHaveTextContent(zh(locale) ? '检查一次，然后创建一次' : 'Review once, then create once');
    expect(screen.getByTestId('experiment-confirm')).toHaveAccessibleName(zh(locale) ? '创建 6 次运行' : 'Create 6 runs');
    expect(api.request).not.toHaveBeenCalled();
    snapshots[locale] = stateSnapshot();
    fireEvent.click(screen.getByTestId('experiment-confirm'));
    expect(await screen.findByTestId('experiment-results')).toBeInTheDocument();
    expect(screen.getByTestId('experiment-id')).toHaveTextContent('exp-1');
    expect(api.request).toHaveBeenCalledTimes(1);
    cleanup();
  }
  expect(snapshots['zh-CN']).toEqual(snapshots['en-US']);
});

it('shows the same validation failure state without claiming a valid version', async () => {
  const invalid: Project = {
    ...project,
    validation: { valid: false, revision: 3, errors: [], issues: [{ source: 'scenario', path: '/run/seed', message: 'Seed must be an integer.' }] },
  };
  const snapshots: Record<string, ReturnType<typeof stateSnapshot>> = {};
  for (const locale of LOCALES) {
    renderConfig(locale, invalid, 'run');
    expect(await screen.findByTestId('wb-issues')).toHaveAttribute('role', 'alert');
    expect(screen.getByTestId('wb-issues')).toHaveTextContent('Seed must be an integer.');
    expectAction('wb-save-version', 'Save version', '保存版本', locale, false);
    expect(action('wb-validate')).toHaveAccessibleName(zh(locale) ? '校验' : 'Validate');
    expect(action('wb-validate')).toBeEnabled();
    snapshots[locale] = stateSnapshot();
    cleanup();
  }
  expect(snapshots['zh-CN']).toEqual(snapshots['en-US']);
});

it('renders faulted initialization as a kept run that cannot start in both locales', async () => {
  const snapshots: Record<string, ReturnType<typeof stateSnapshot>> = {};
  for (const locale of LOCALES) {
    mocks.runSession = undefined;
    vi.mocked(WorkbenchApi.prototype.runs).mockResolvedValue([faulted]);
    vi.mocked(WorkbenchApi.prototype.initialize).mockResolvedValue(faulted);
    renderWorkbench(locale, '/w/project');
    fireEvent.click(await screen.findByTestId('wb-init'));
    const alert = await screen.findByTestId('wb-init-failed');
    expect(alert).toHaveAttribute('role', 'alert');
    expect(alert).toHaveTextContent(zh(locale) ? '初始化失败' : 'Initialization failed');
    expect(alert).toHaveTextContent(zh(locale) ? '运行出错' : 'Faulted');
    expect(alert).toHaveTextContent(zh(locale) ? '不能启动' : 'cannot be started');
    expect(screen.queryByTestId('wb-start')).toBeNull();
    snapshots[locale] = stateSnapshot();
    cleanup();
  }
  expect(snapshots['zh-CN']).toEqual(snapshots['en-US']);
});

it('replays the same recorded moment and offers the same exit action in both locales', async () => {
  const snapshots: Record<string, ReturnType<typeof stateSnapshot>> = {};
  for (const locale of LOCALES) {
    const session = recordedSession();
    vi.mocked(WorkbenchApi.prototype.runs).mockResolvedValue([run]);
    renderWorkbench(locale, '/w/project?run=run-1&mode=replay');
    const status = await screen.findByTestId('wb-status');
    expect(status).toHaveTextContent(zh(locale) ? '已完成' : 'Completed');
    expect(status).toHaveTextContent(zh(locale) ? '回放' : 'replay');
    expect(screen.getByTestId('wb-playback')).toHaveAccessibleName(zh(locale) ? '播放已记录的时间轴' : 'Play recorded timeline');
    expect(screen.getByTestId('wb-back-live')).toHaveAccessibleName(zh(locale) ? '跳到结束时刻 L' : 'Jump to the end L');
    expect(screen.getByLabelText(zh(locale) ? '已记录的时间轴' : 'Recorded timeline')).toHaveValue('1');
    snapshots[locale] = stateSnapshot();
    cleanup();
  }
  expect(snapshots['zh-CN']).toEqual(snapshots['en-US']);
});

it('compares the same runs and finality state in both locales', async () => {
  const response = {
    runs: [
      { id: 'r-1', title: 'Normal delivery', title_zh: '普通配送', template_id: 'food-delivery', kpis: { tasks_completed: { value: 4, unit: 'count', label: 'Tasks completed' } }, knobs: { mode: 'normal' }, algorithms: {} },
      { id: 'r-2', title: 'Rush delivery', title_zh: '加急配送', template_id: 'food-delivery', kpis: { tasks_completed: { value: 5, unit: 'count', label: 'Tasks completed' } }, knobs: { mode: 'rush' }, algorithms: {} },
    ],
    alignment: 'recorded_run_end',
    recorded_ends: { 'r-1': { at_ns: '120000000000', commit_index: 2, status: 'completed' }, 'r-2': { at_ns: '120000000000', commit_index: 2, status: 'completed' } },
    config_diff: [{ path: '/knobs/mode', values: { 'r-1': 'normal', 'r-2': 'rush' }, present: { 'r-1': true, 'r-2': true } }],
  };
  const snapshots: Record<string, ReturnType<typeof stateSnapshot>> = {};
  for (const locale of LOCALES) {
    const api = {
      request: vi.fn().mockResolvedValue(response), templates: vi.fn().mockResolvedValue([template]), algorithms: vi.fn().mockResolvedValue([]),
    } as unknown as TemplateApi;
    installLocale(locale);
    render(<I18nProvider><CompareRuns api={api} ids={['r-1', 'r-2']} onBack={vi.fn()} /></I18nProvider>);
    expect(await screen.findByRole('heading', { name: zh(locale) ? '运行 1 · 普通配送' : 'Run 1 · Normal delivery' })).toBeVisible();
    expect(screen.getByTestId('comparison-summary')).toHaveAccessibleName(zh(locale) ? '对比总览' : 'Comparison summary');
    expect(screen.getByTestId('comparison-state-r-1')).toHaveTextContent(zh(locale) ? '暂定' : 'Provisional');
    expect(screen.getByRole('button', { name: zh(locale) ? '返回' : 'Go back' })).toBeEnabled();
    snapshots[locale] = stateSnapshot();
    cleanup();
  }
  expect(snapshots['zh-CN']).toEqual(snapshots['en-US']);
});

it('keeps English-only installed metadata original inside the Chinese gallery', async () => {
  installLocale('zh-CN');
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('not found', { status: 404 })));
  const api = { base: '/api', templates: vi.fn().mockResolvedValue([externalTemplate]), projects: vi.fn().mockResolvedValue([]), guide: vi.fn().mockResolvedValue({ markdown: '# Patrol' }) } as unknown as WorkbenchApi;
  render(<I18nProvider><Gallery api={api} onOpenProject={vi.fn()} onCreate={vi.fn()} onCreateBlank={vi.fn()} /></I18nProvider>);
  const card = await screen.findByTestId('wb-template-external-patrol');
  expect(within(card).getByText('External patrol')).toBeVisible();
  expect(card).toHaveTextContent('A perimeter declared by an installed contribution.');
  expect(screen.getByTestId('wb-gallery')).toHaveTextContent('1 个现成场景');
  // Metadata is not fabricated when the optional _zh sibling is absent.
  expect(localized(externalTemplate, 'title', 'zh-CN')).toBe('External patrol');
  expect(localized(externalTemplate.knobs[0], 'label', 'zh-CN')).toBe('Patrol altitude (m)');
  expect(translate('zh-CN', externalTemplate.title)).toBe('External patrol');
});

it('renders a model reply unchanged in both locales', async () => {
  const reply = 'Rotation applied: 3 drones remain and battery is 61%.';
  const connection: Connection = { configured: true, source: 'operator', base_url: 'http://api', model: 'test', has_key: true, status: 'ok', detail: null, tested_at: null };
  for (const locale of LOCALES) {
    installLocale(locale);
    const session = {
      id: 's', created_at: '', updated_at: '', workspace_id: 'project', title: 'Scene', busy: false, last_seq: 1,
      events: [{ seq: 1, at: '', kind: 'assistant' as const, text: reply, source: 'model' as const }],
      plan: null, plan_versions: [], alternatives: [], applied: [],
    } satisfies Session;
    const assistant: AssistantSession = {
      session, events: session.events, plan: undefined, applied: undefined, busy: false, pending: undefined, error: undefined,
      send: vi.fn(), choose: vi.fn(), cancel: vi.fn(), apply: vi.fn(), refresh: vi.fn(),
    };
    render(<I18nProvider><AssistantPanel assistant={assistant} connection={connection} connectionSaving={false}
      onConnect={async () => undefined} onTest={async () => undefined} onClose={vi.fn()} onShowPlan={vi.fn()} onShowChanges={vi.fn()} /></I18nProvider>);
    const transcript = screen.getByTestId('as-transcript');
    expect(transcript).toHaveTextContent(reply);
    expect(transcript).not.toHaveTextContent('轮换已应用');
    cleanup();
  }
});
