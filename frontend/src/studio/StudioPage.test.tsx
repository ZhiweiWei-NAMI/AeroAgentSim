import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { Modal } from 'antd';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { I18nProvider } from '../i18n/I18nProvider';
import { StudioApi, type Workspace } from './api';
import StudioPage, { workspaceUrl } from './StudioPage';

vi.mock('../i18n/messages', async () => ({ default: { 'zh-CN': (await import('../i18n/studio-messages')).default } }));
vi.mock('./ScenePreview', () => ({ ScenePreview: () => <div data-testid="preview" /> }));
vi.mock('./RegionMap', () => ({ RegionMap: () => <div data-testid="region-preview" /> }));
vi.mock('../newcomers/AlgorithmsPanel', () => ({ AlgorithmsPanel: () => null }));
vi.mock('./steps/DomainPluginsStep', () => ({ DomainPluginsStep: () => null }));
vi.mock('./steps/EntitiesStep', () => ({ EntitiesStep: () => null }));
vi.mock('./steps/PredicatesStep', () => ({ PredicatesStep: () => null }));
vi.mock('./steps/RulesStep', () => ({ RulesStep: () => null }));
vi.mock('./steps/AgentsStep', () => ({ AgentsStep: () => null }));
vi.mock('../console/Notifications', () => ({ useConsoleNotice: () => () => {} }));
beforeEach(() => {
  window.localStorage.clear();
  vi.restoreAllMocks();
  vi.stubGlobal('matchMedia', vi.fn(() => ({ matches: false, addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {} })));
});
afterEach(async () => {
  cleanup();
  // Static antd confirmations have their own React roots, outside RTL cleanup.
  act(() => Modal.destroyAll());
  await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  window.localStorage.clear();
  vi.unstubAllGlobals();
});
function mount({ region = false, locale = 'en-US' } = {}) {
  window.localStorage.setItem('aeroagentsim.locale', locale);
  const types = [{ id: 'oo:UAV', name: 'UAV', parents: [], abstract: false }];
  let first: Workspace = { id: 'first', name: 'First', scenario: { id: 'parcel-demo', entities: [{ id: 'alpha', type: 'oo:UAV', facts: {} }], engines: {} }, demo_console: { city_available: true, capture_mode: 'city' }, ...(region ? { region: { extract: 'map', bounds: [0, 0, 1, 1], alt: 0, level_height_m: 3 } } : {}) };
  const second: Workspace = { id: 'second', name: 'Second', scenario: { id: 'other-demo', entities: [], engines: {} } };
  const request = vi.spyOn(StudioApi.prototype, 'request').mockImplementation(async (path, body) => {
    if (path === '/v1/studio/catalog') return { extracts: [{ id: 'map', name: 'Local map', bounds: [0, 0, 1, 1] }], engines: [] } as any;
    if (path.startsWith('/v1/studio/types?')) return { types, fields: [], relations: [] } as any;
    if (path === '/v1/studio/workspaces') return [first, second] as any;
    if (path === '/v1/studio/workspaces/first/validate') { first = { ...first, scenario: (body as any).scenario, validation: { valid: true, errors: [] } }; return first.validation as any; }
    if (path === '/v1/studio/workspaces/first') { if (body) first = { ...first, ...(body as any), validation: undefined }; return first as any; }
    if (path === '/v1/studio/workspaces/second') return second as any;
    throw new Error(`Unexpected test request: ${path}`);
  });
  render(<I18nProvider><MemoryRouter initialEntries={['/studio?workspace=first']}><StudioPage /></MemoryRouter></I18nProvider>);
  return request;
}
it('does not call every template a traffic demo and explains validation before running', async () => {
  mount();
  expect(await screen.findByText('Demo scene · original city geometry')).toBeInTheDocument();
  expect(screen.getByText('No geographic extract selected. This scenario uses its authored local scene.')).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Run now' })).toBeDisabled();
  await waitFor(() => expect(screen.getByRole('button', { name: 'Validate' })).toBeEnabled());
  fireEvent.click(screen.getByRole('button', { name: 'Validate' }));
  await waitFor(() => expect(screen.getByRole('button', { name: 'Run now' })).toBeEnabled());
  fireEvent.change(screen.getByLabelText('Workspace name'), { target: { value: 'Changed draft' } });
  expect(screen.getByRole('button', { name: 'Run now' })).toBeDisabled();
  expect(screen.getByTestId('run-now-hint')).toHaveTextContent('Validate your changes first');
});
it('clears geographic selection when switching to a workspace without a region', async () => {
  mount({ region: true });
  expect(await screen.findByTestId('region-preview')).toBeInTheDocument();
  await waitFor(() => expect(screen.getByTestId('workspace-picker')).toBeEnabled());
  fireEvent.click(screen.getByTestId('workspace-picker'));
  fireEvent.click(await screen.findByText('Second'));
  await waitFor(() => expect(screen.queryByTestId('region-preview')).toBeNull());
  expect(screen.getByLabelText('Workspace name')).toHaveValue('Second');
  expect(screen.getByText('No geographic extract selected. This scenario uses its authored local scene.')).toBeInTheDocument();
});
it('keeps the switched workspace in the URL and removes stale template/entity context', () => {
  const url = new URL(workspaceUrl('?api=http%3A%2F%2Fapi.test&lang=zh&template=traffic-accident&entity=alpha&workspace=first', 'second'), 'http://console.test');
  expect(url.searchParams.get('workspace')).toBe('second');
  expect(url.searchParams.get('api')).toBe('http://api.test');
  expect(url.searchParams.get('lang')).toBe('zh');
  expect(url.searchParams.has('entity')).toBe(false);
  expect(url.searchParams.has('template')).toBe(false);
});
it('asks before discarding unsaved edits on a workspace switch', async () => {
  const request = mount();
  await screen.findByTestId('preview');
  fireEvent.change(screen.getByLabelText('Workspace name'), { target: { value: 'My unsaved draft' } });
  await waitFor(() => expect(screen.getByTestId('workspace-picker')).toBeEnabled());
  fireEvent.click(screen.getByTestId('workspace-picker'));
  fireEvent.click(await screen.findByText('Second'));
  const confirmation = await screen.findByRole('dialog');
  expect(within(confirmation).getByText('Switch without saving?')).toBeInTheDocument();
  fireEvent.click(within(confirmation).getByRole('button', { name: 'Keep editing' }));
  await waitFor(() => expect(confirmation).not.toBeInTheDocument());
  expect(screen.getByLabelText('Workspace name')).toHaveValue('My unsaved draft');
  expect(request.mock.calls.some(([path]) => path === '/v1/studio/workspaces/second')).toBe(false);
});
it('prevents placing an entity with an existing trimmed ID', async () => {
  mount();
  await screen.findByTestId('preview');
  fireEvent.change(screen.getByLabelText('Entity ID'), { target: { value: ' alpha ' } });
  fireEvent.mouseDown(screen.getByRole('combobox', { name: 'Placement type' }));
  fireEvent.click(await screen.findByTitle('UAV'));
  expect(screen.getByRole('button', { name: 'Place entity' })).toBeDisabled();
  expect(screen.getByRole('alert')).toHaveTextContent('This entity ID already exists. Choose a different name.');
});
it('localizes all seven steps, the progress label and scene controls', async () => {
  mount({ locale: 'zh-CN' });
  await screen.findByTestId('preview');
  expect(screen.getByTestId('studio-step-scene')).toHaveTextContent('场景');
  expect(screen.getByTestId('studio-step-entities')).toHaveTextContent('实体');
  expect(screen.getByTestId('studio-step-plugins')).toHaveTextContent('领域插件');
  expect(screen.getByTestId('studio-step-predicates')).toHaveTextContent('谓词');
  expect(screen.getByTestId('studio-step-rules')).toHaveTextContent('事件链与规则');
  expect(screen.getByTestId('studio-step-agents')).toHaveTextContent('智能体');
  expect(screen.getByTestId('studio-step-validate')).toHaveTextContent('验证并运行');
  expect(screen.getByText('第 1 步，共 7 步')).toBeInTheDocument();
  expect(screen.getByRole('button', { name: '放置实体' })).toBeInTheDocument();
  expect(screen.getByLabelText('实体 ID')).toBeInTheDocument();
  expect(screen.getByRole('button', { name: '下一步' })).toBeInTheDocument();
});
