import { beforeAll, afterEach, expect, it, vi } from 'vitest';
import { cleanup } from '@testing-library/react';
import { fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { I18nProvider } from '../i18n/I18nProvider';
import AeroGraphPage from './AeroGraphPage';

beforeAll(() => {
  Object.defineProperty(window, 'matchMedia', { writable: true, value: vi.fn().mockImplementation(query => ({ matches: false, media: query, onchange: null, addListener: vi.fn(), removeListener: vi.fn(), addEventListener: vi.fn(), removeEventListener: vi.fn(), dispatchEvent: vi.fn() })) });
});

afterEach(cleanup);

const payload = {
  workspace: { id: 'studio-1', name: 'Traffic accident (demo)' },
  types: [
    { id: 'oo:ModelObject', name: { 'en-US': 'Model object', 'zh-CN': '模型对象' }, description: 'Root contract', parents: [], abstract: true, count: null },
    { id: 'oo:Vehicle', name: { 'en-US': 'Vehicle', 'zh-CN': '载具' }, description: 'A powered vehicle', parents: ['oo:PhysicalAsset', 'oo:ModelObject'], abstract: false, count: 12 },
    { id: 'oo:PhysicalAsset', name: { 'en-US': 'Physical asset', 'zh-CN': '物理资产' }, description: '', parents: ['oo:ModelObject'], abstract: false, count: 30 },
  ],
  fields: [
    {
      id: 'exp.field.physical.vehicle.currentOperationalMassKg', name: '当前运行构型总质量',
      description: '', declaring_type: 'oo:Vehicle',
      schema: { type: 'number', unit: { symbol: 'kg' }, nullable: false }, metadata: { domain: 'physical' },
      writers: [{ engine_id: 'road', plugin: 'road_motion', entities: ['vehicle-1'] }],
    },
  ],
  relations: [
    { id: 'oo:relation:sensor-output-channel', displayName: '传感器输出通道', sourceClass: 'oo:Vehicle', targetClass: 'oo:PhysicalAsset', kind: 'has_a' },
  ],
  predicates: [
    { id: 'cap.predicate.radio_frequency', name: '信号参考频率落入选定频段', description: 'Matches vehicle mass.', definition: { entityTypeIds: ['oo:Vehicle'], op: 'and', args: [] } },
  ],
  chains: [],
  entities: [{ id: 'vehicle-1', type: 'oo:Vehicle', label: 'Vehicle 1' }],
  workspaces: [{ id: 'studio-1', name: 'Traffic accident (demo)' }],
};

function mockFetch(body: unknown, ok = true) {
  return vi.fn(async () => new Response(JSON.stringify(body), { status: ok ? 200 : 503, headers: { 'Content-Type': 'application/json' } }));
}

function renderPage() {
  return render(<MemoryRouter initialEntries={['/aerograph?type=oo%3AVehicle']}><I18nProvider><AeroGraphPage /></I18nProvider></MemoryRouter>);
}

it('renders the tree, ancestry breadcrumb and inherited fields without raw ids as headings', async () => {
  vi.stubGlobal('fetch', mockFetch(payload));
  renderPage();
  await screen.findByRole('heading',{name:'Vehicle'});
  expect(screen.getByText('载具')).toBeTruthy();
  expect(screen.getAllByText('Model object').length).toBeGreaterThan(0);
  expect(screen.getByText('当前运行构型总质量')).toBeTruthy();
  expect(screen.getByText(/Road motion/)).toBeTruthy();
  expect(screen.getByText('信号参考频率落入选定频段')).toBeTruthy();
  expect(screen.getByText('Vehicle → Physical asset')).toBeTruthy();
  expect(screen.getByText('12 in workspace')).toBeTruthy();
});

it('shows an error state with retry when the endpoint fails', async () => {
  vi.stubGlobal('fetch', mockFetch({ detail: 'ontology source unavailable' }, false));
  renderPage();
  await screen.findByText('The explorer catalog is unavailable');
  expect(screen.getByRole('button', { name: 'Retry' })).toBeTruthy();
  expect(screen.queryByTestId('aerograph-tree')).toBeNull();
});

it('loads the workspace through the endpoint and links workspace entities', async () => {
  const fetchMock = mockFetch(payload);
  vi.stubGlobal('fetch', fetchMock);
  renderPage();
  await screen.findByRole('heading',{name:'Vehicle'});
  expect(fetchMock).toHaveBeenCalledWith(expect.stringContaining('/v1/studio/explorer'), undefined);
  expect(screen.getByRole('link', { name: 'Vehicle 1' }).getAttribute('href')).toContain('/studio');
  expect(screen.getByRole('link',{name:'Vehicle 1'}).getAttribute('href')).toContain('workspace=studio-1');
});
