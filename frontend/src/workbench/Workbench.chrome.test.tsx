import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { MemoryRouter } from 'react-router-dom';
import { I18nProvider } from '../i18n/I18nProvider';
import { TemplateApi } from '../newcomers/api';
import Workbench from './Workbench';
import { WorkbenchApi, type Project, type RunRecord } from './api';
import { emptyCommit, fixtureHeader } from '../behaviours/extension-fixture';
import { TemporalFeedStore } from '../feeds/temporal-store';
import { PlaybackClock } from '../viewport/clock';
import type { RunSession } from './useRunSession';

const stylesDirectory = dirname(fileURLToPath(import.meta.url));
const workbenchStyles = readFileSync(resolve(stylesDirectory, 'workbench.css'), 'utf8');
const runStatusStyles = readFileSync(resolve(stylesDirectory, 'run-status.css'), 'utf8');

const mocks = vi.hoisted(() => ({ readSession: vi.fn(), runSession: undefined as RunSession | undefined }));
vi.mock('../assistant/AssistantPanel', () => ({ AssistantPanel: () => null }));
vi.mock('../assistant/motion/NeuralGlyph', () => ({ NeuralGlyph: () => null }));
vi.mock('../studio/ScenePreview', () => ({ ScenePreview: () => null }));
vi.mock('../viewport/NonspatialViews', () => ({ NonspatialViews: () => null }));
vi.mock('./useRunSession', async original => ({ ...await original<object>(), useRunSession: (...args: unknown[]) => { mocks.readSession(...args); return mocks.runSession; } }));
vi.mock('../assistant/useAssistantMode', () => ({ useAssistantMode: () => ({
  api: {}, assistant: { session: undefined, plan: undefined, applied: [], busy: false, send: vi.fn(), apply: vi.fn() },
  connection: { connection: {}, save: vi.fn(), test: vi.fn() }, stage: 'proposed', board: true,
  lastSection: { current: 'run' }, setFocus: vi.fn(), setViewing: vi.fn(), closeBoard: vi.fn(), openBoard: vi.fn(), switchMode: vi.fn(), applyDone: vi.fn(),
}) }));

const project: Project = { id: 'project', name: 'Project', revision: 4, updated_at: '', dirty: false, scenario: { run: { seed: 43 } },
  latest_version: { version: 2, revision: 4, digest: 'v2', created_at: '', validation: { valid: true, revision: 4, errors: [] } } };
const run = { id: 'run', status: 'completed', scenario: 'project', studio_workspace: 'project', workspace_version: 1, template_id: 'rescue' } as unknown as RunRecord;

beforeEach(() => {
  vi.stubGlobal('ResizeObserver', class { observe() {} disconnect() {} });
  localStorage.clear();
  mocks.runSession = undefined;
  mocks.readSession.mockClear();
  vi.spyOn(WorkbenchApi.prototype, 'project').mockResolvedValue(project);
  vi.spyOn(WorkbenchApi.prototype, 'runs').mockResolvedValue([]);
  vi.spyOn(WorkbenchApi.prototype, 'initialize').mockResolvedValue(run);
  vi.spyOn(WorkbenchApi.prototype, 'speed').mockResolvedValue({ id: run.id, factor: 1 });
  vi.spyOn(TemplateApi.prototype, 'kpis').mockResolvedValue({ run_id: run.id, final: {}, series: [] });
  vi.spyOn(TemplateApi.prototype, 'template').mockResolvedValue({ id: 'rescue', title: 'Rescue', kpis: ['a', 'b', 'c', 'd', 'e'] } as never);
});
afterEach(() => { cleanup(); vi.useRealTimers(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });
function setup(entry: string) {
  return render(<I18nProvider><MemoryRouter initialEntries={[entry]}><Workbench /></MemoryRouter></I18nProvider>);
}

function sessionWith(status: 'running' | 'completed', cuts = 3) {
  const store = new TemporalFeedStore({ ...fixtureHeader, presentation: [] });
  for (let index = 1; index <= cuts; index++) store.ingest(emptyCommit(index, String(index * 1000)));
  const clock = new PlaybackClock('0', String(cuts * 1000));
  clock.seek(clock.end); store.seek(clock.ns);
  const session: RunSession = {
    store, clock, status, journalComplete: true, followingLive: false, revision: 1,
    inputSource: { serialize: vi.fn() }, seekOffset: vi.fn(), seekCut: vi.fn(), togglePlayback: vi.fn(),
    backToLive: vi.fn(() => { clock.seek(clock.end); session.cut = undefined; session.followingLive = true; store.seek(clock.ns); }), setSpeed: vi.fn(),
  };
  mocks.runSession = session;
  vi.mocked(WorkbenchApi.prototype.runs).mockResolvedValue([{ ...run, status }]);
  return session;
}

it('S2: renders one centred glass KPI strip with dividers, 52px tall', async () => {
  sessionWith('completed');
  setup(`/w/project?run=${run.id}&mode=replay`);
  const strip = await screen.findByTestId('wb-kpis');
  await waitFor(() => expect(strip.querySelectorAll('.wb-kpi').length).toBe(5));
  expect(strip.className).not.toContain('wb-glass');
  expect(workbenchStyles).toMatch(/\.wb-kpis\s*\{[^}]*height:\s*52px/s);
  expect(workbenchStyles).toMatch(/\.wb-kpis\s*\{[^}]*justify-content:\s*center/s);
  expect(workbenchStyles).toMatch(/\.wb-kpi \+ \.wb-kpi[^{]*\{[^}]*border-left:\s*1px solid var\(--wb-line\)/s);
  // Values keep the em-dash empty state and the help tooltip.
  expect(strip.querySelector('.wb-kpi strong')!.textContent).toBe('—');
  expect(strip.querySelector('.wb-kpi')!.getAttribute('title')).toContain('a');
});

it('S2: below 1100px the strip shows three metrics plus a "+2" popover chip', async () => {
  const listeners: Array<() => void> = [];
  let narrow = false;
  vi.stubGlobal('matchMedia', vi.fn().mockImplementation((query: string) => ({
    get matches() { return narrow && query.includes('1100'); },
    media: query, addEventListener: (_: string, fn: () => void) => listeners.push(fn), removeEventListener: (_: string, fn: () => void) => { const at = listeners.indexOf(fn); if (at >= 0) listeners.splice(at, 1); },
    addListener: () => {}, removeListener: () => {}, onchange: null, dispatchEvent: () => false,
  })));
  sessionWith('completed');
  const view = setup(`/w/project?run=${run.id}&mode=replay`);
  await screen.findByTestId('wb-kpis');
  const flip = () => { narrow = !narrow; listeners.forEach(fn => fn()); };
  act(flip);
  const strip = screen.getByTestId('wb-kpis');
  expect(strip.querySelectorAll(':scope > .wb-kpi').length).toBe(3);
  const chip = screen.getByTestId('wb-kpi-more');
  expect(chip).toHaveTextContent('+2');
  fireEvent.click(chip.querySelector('summary')!);
  expect(chip.querySelectorAll('.wb-kpi').length).toBe(2);
  act(flip);
  await waitFor(() => expect(screen.queryByTestId('wb-kpi-more')).toBeNull());
  expect(screen.getByTestId('wb-kpis').querySelectorAll('.wb-kpi').length).toBe(5);
  view.unmount();
});

it('S3: with a run open the objects panel starts collapsed and the choice is remembered', async () => {
  sessionWith('completed');
  const view = setup(`/w/project?run=${run.id}&mode=replay`);
  await screen.findByTestId('wb-top');
  await waitFor(() => expect(screen.queryByTestId('wb-objects')).toBeNull());
  expect(localStorage.getItem('aeroagentsim.objectsOpen')).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: /Objects/ }));
  expect(await screen.findByTestId('wb-objects')).toBeVisible();
  expect(localStorage.getItem('aeroagentsim.objectsOpen')).toBe('1');
  fireEvent.click(screen.getByRole('button', { name: 'Hide objects' }));
  await waitFor(() => expect(screen.queryByTestId('wb-objects')).toBeNull());
  expect(localStorage.getItem('aeroagentsim.objectsOpen')).toBe('0');
  view.unmount();
  setup(`/w/project?run=${run.id}&mode=replay`);
  await screen.findByTestId('wb-top');
  // Closing in the previous visit is remembered.
  await waitFor(() => expect(screen.queryByTestId('wb-objects')).toBeNull());
  expect(screen.getByRole('button', { name: /Objects/ })).toBeVisible();
});

it('S3: the inspector collapses to its tab when nothing is selected and reopens on selection', async () => {
  sessionWith('completed');
  const view = setup(`/w/project?run=${run.id}&mode=replay`);
  await screen.findByTestId('wb-top');
  await waitFor(() => expect(screen.queryByTestId('wb-inspector')).toBeNull());
  expect(screen.getByRole('button', { name: /Inspector/ })).toBeVisible();
  // Reopening by hand, with still nothing selected, keeps it open.
  fireEvent.click(screen.getByRole('button', { name: /Inspector/ }));
  expect(await screen.findByTestId('wb-inspector')).toBeVisible();
  view.unmount();
});

it('S3: the collapsed objects tab shows the count', async () => {
  sessionWith('completed');
  const view = setup(`/w/project?run=${run.id}&mode=replay`);
  await screen.findByTestId('wb-top');
  // The tab announces the entity count next to the name.
  expect(await screen.findByRole('button', { name: /^☰? ?Objects \d+$/ })).toBeVisible();
  view.unmount();
});

it('S1: notices stack in .wb-notices as pills, keeping the container for other alerts', async () => {
  sessionWith('completed');
  const view = setup(`/w/project?run=${run.id}&mode=replay`);
  await screen.findByTestId('wb-top');
  expect(workbenchStyles).toMatch(/\.wb-notice-pill\s*\{[^}]*border-radius:\s*999px/s);
  expect(workbenchStyles).toMatch(/\.wb-notice-pill\s*\{[^}]*background:\s*rgba\(178,\s*107,\s*255,\s*\.16\)/s);
  expect(runStatusStyles).toMatch(/\.wb-notices\s*\{[^}]*flex-direction:\s*column/s);
  expect(runStatusStyles).toMatch(/\.wb-notices\s*>\s*\.wb-history\s*\{[^}]*position:\s*static/s);
  view.unmount();
});
