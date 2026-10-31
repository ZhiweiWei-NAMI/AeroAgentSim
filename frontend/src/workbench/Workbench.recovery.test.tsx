import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { MemoryRouter } from 'react-router-dom';
import App from '../App';
import { TemplateApi } from '../newcomers/api';
import { WorkbenchApi, type Project } from './api';

const failures = vi.hoisted(() => ({ scene: false, configuration: false, workbench: false }));
vi.mock('../console/ApiConnection', () => ({ ApiConnection: () => null }));
vi.mock('../assistant/motion/NeuralGlyph', () => ({ NeuralGlyph: () => null }));
vi.mock('../studio/ScenePreview', () => ({ ScenePreview: () => {
  if (failures.scene) throw Error('Scene render failed');
  return <div data-testid="recovered-scene">Scene preview</div>;
} }));
vi.mock('./ConfigPanel', () => ({ ConfigPanel: () => {
  if (failures.configuration) throw Error('Configuration render failed');
  return <aside data-testid="recovered-configuration">Configuration</aside>;
} }));
vi.mock('./useRunSession', async original => ({ ...await original<object>(), useRunSession: () => {
  if (failures.workbench) throw Error('Workbench render failed');
  return undefined;
} }));
vi.mock('../assistant/useAssistantMode', () => ({ useAssistantMode: () => ({
  api: {}, assistant: { plan: undefined, busy: false }, connection: {}, stage: 'proposed',
  lastSection: { current: 'controls' },
}) }));

const project: Project = { id: 'project', name: 'Project', revision: 2, updated_at: '', dirty: true,
  scenario: {}, latest_version: null };

beforeEach(() => {
  failures.scene = failures.configuration = failures.workbench = false;
  localStorage.clear();
  sessionStorage.clear();
  vi.stubGlobal('ResizeObserver', class { observe() {} disconnect() {} });
  vi.spyOn(console, 'error').mockImplementation(() => undefined);
  vi.spyOn(WorkbenchApi.prototype, 'project').mockResolvedValue(project);
  vi.spyOn(WorkbenchApi.prototype, 'runs').mockResolvedValue([]);
  vi.spyOn(TemplateApi.prototype, 'template').mockRejectedValue(Error('No template'));
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

function setup(locale: string) {
  localStorage.setItem('aeroagentsim.locale', locale);
  return render(<MemoryRouter initialEntries={['/w/project?panel=controls']}><App /></MemoryRouter>);
}
const retryLabel = (locale: string) => locale === 'zh-CN' ? '重试' : 'Retry';
const reloadLabel = (locale: string) => locale === 'zh-CN' ? '刷新页面' : 'Reload page';

it.each(['en-US', 'zh-CN'])('keeps the scene and command bar when configuration fails, and retries in %s', async locale => {
  failures.configuration = true;
  setup(locale);
  expect(await screen.findByTestId('wb-config-failed')).toHaveAttribute('role', 'alert');
  expect(screen.getByTestId('wb-top')).toBeVisible();
  expect(await screen.findByTestId('recovered-scene')).toBeVisible();
  expect(screen.getByRole('button', { name: reloadLabel(locale) })).toBeVisible();
  failures.configuration = false;
  fireEvent.click(screen.getByRole('button', { name: retryLabel(locale) }));
  expect(await screen.findByTestId('recovered-configuration')).toBeVisible();
});

it.each(['en-US', 'zh-CN'])('keeps configuration usable when the scene fails, and retries in %s', async locale => {
  failures.scene = true;
  setup(locale);
  expect(await screen.findByTestId('wb-scene-failed')).toHaveTextContent(locale === 'zh-CN' ? '无法显示场景' : 'Could not display the scene');
  expect(screen.getByTestId('wb-top')).toBeVisible();
  expect(await screen.findByTestId('recovered-configuration')).toBeVisible();
  expect(screen.getByRole('button', { name: reloadLabel(locale) })).toBeVisible();
  failures.scene = false;
  fireEvent.click(screen.getByRole('button', { name: retryLabel(locale) }));
  expect(await screen.findByTestId('recovered-scene')).toBeVisible();
});

it.each(['en-US', 'zh-CN'])('shows recovery instead of an empty root when the workbench fails in %s', async locale => {
  failures.workbench = true;
  const view = setup(locale);
  expect(await screen.findByRole('alert')).toHaveTextContent(locale === 'zh-CN' ? '无法显示工作台' : 'Could not display the workbench');
  expect(view.container).not.toBeEmptyDOMElement();
  expect(screen.getByRole('button', { name: reloadLabel(locale) })).toBeVisible();
  failures.workbench = false;
  fireEvent.click(screen.getByRole('button', { name: retryLabel(locale) }));
  expect(await screen.findByTestId('wb-top')).toBeVisible();
  expect(await screen.findByTestId('recovered-configuration')).toBeVisible();
});
