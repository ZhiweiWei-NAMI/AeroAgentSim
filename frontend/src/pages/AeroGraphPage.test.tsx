import { beforeAll, afterEach, expect, it, vi } from 'vitest';
import { cleanup } from '@testing-library/react';
import { fireEvent, render, screen, within } from '@testing-library/react';
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
    { id: 'oo:UnusedCatalogType', name: { 'en-US': 'Unused catalog type' }, description: '', parents: ['oo:ModelObject'], abstract: false, count: 0 },
  ],
  fields: [
    {
      id: 'exp.field.physical.vehicle.currentOperationalMassKg', name: '当前运行构型总质量',
      description: '', declaring_type: 'oo:Vehicle',
      schema: { type: 'number', unit: { symbol: 'kg' }, nullable: false }, metadata: { domain: 'physical' },
      writers: [{ engine_id: 'road', plugin: 'road_motion', entities: ['vehicle-1'] }],
    },
    {
      id: 'exp.field.core.modelObject.composition', name: { 'en-US': 'Composition', 'zh-CN': '构成' },
      description: '', declaring_type: 'oo:ModelObject',
      schema: { type: 'object', members: { x: { type: 'number' }, y: { type: 'number' } } }, metadata: {},
      writers: [],
    },
  ],
  relations: [
    { id: 'oo:relation:sensor-output-channel', displayName: '传感器输出通道', sourceClass: 'oo:Vehicle', targetClass: 'oo:PhysicalAsset', kind: 'has_a' },
  ],
  predicates: [
    { id: 'cap.predicate.radio_frequency', name: '信号参考频率落入选定频段', description: 'Native catalog predicate.', definition: { entityTypeIds: ['oo:Vehicle'], op: 'and', args: [] }, origin: 'native' },
    { id: 'ws.predicate.mass_window', name: { 'en-US': 'Mass within window', 'zh-CN': '质量落入选定区间' }, description: 'Workspace predicate on vehicle mass.', definition: { entityTypeIds: ['oo:Vehicle'], op: 'and', args: [] }, package:0 },
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
  expect(screen.queryByText('载具')).toBeNull();
  expect(screen.getAllByText('Model object').length).toBeGreaterThan(0);
  expect(screen.getByText('Current Operational Mass Kg')).toBeTruthy();
  expect(screen.getByText(/Road Motion/)).toBeTruthy();
  // Workspace behaviour shows with an English-primary label; the native
  // catalog predicate (no workspace usage) stays out of the reference list.
  expect(screen.getByText('Mass within window')).toBeTruthy();
  expect(screen.queryByText('质量落入选定区间')).toBeNull();
  expect(screen.queryByText('信号参考频率落入选定频段')).toBeNull();
  expect(screen.getByText('Vehicle → Physical asset')).toBeTruthy();
  expect(screen.getByText('12 in workspace')).toBeTruthy();
});

it('summarizes value schemas in friendly English with an expandable schema detail', async () => {
  vi.stubGlobal('fetch', mockFetch(payload));
  renderPage();
  await screen.findByRole('heading',{name:'Vehicle'});
  expect(screen.getByText('Number · kg')).toBeTruthy();
  expect(screen.getByText('Record · 2 fields')).toBeTruthy();
  const triggers = screen.getAllByTestId('details-trigger');
  const schemaTrigger = triggers.filter(button => button.textContent === 'Schema').at(-1);
  expect(schemaTrigger).toBeTruthy();
  fireEvent.click(schemaTrigger!);
  const dialog = await screen.findByRole('dialog');
  expect(within(dialog).getByText(/"members"/)).toBeTruthy();
});

it('filters the tree to workspace-used types and clears the filter on demand', async () => {
  vi.stubGlobal('fetch', mockFetch(payload));
  renderPage();
  await screen.findByRole('heading',{name:'Vehicle'});
  const tree = screen.getByTestId('aerograph-tree');
  expect(within(tree).getByText('Vehicle')).toBeTruthy();
  expect(within(tree).getByText('Physical asset')).toBeTruthy(); // kept ancestor
  expect(within(tree).queryByText('Unused catalog type')).toBeNull();
  fireEvent.click(screen.getByTestId('aerograph-clear-type-filter'));
  expect(within(tree).getByText('Unused catalog type')).toBeTruthy();
  fireEvent.click(screen.getByTestId('aerograph-clear-type-filter'));
  expect(within(tree).queryByText('Unused catalog type')).toBeNull();
});

it('searches types by English label, localized name and id', async () => {
  vi.stubGlobal('fetch', mockFetch(payload));
  renderPage();
  await screen.findByRole('heading',{name:'Vehicle'});
  const tree = screen.getByTestId('aerograph-tree');
  const search = screen.getByLabelText('Search types');
  fireEvent.click(screen.getByTestId('aerograph-clear-type-filter'));
  fireEvent.change(search, { target: { value: 'unused' } });
  expect(within(tree).getByText('Unused catalog type')).toBeTruthy();
  fireEvent.change(search, { target: { value: '载具' } });
  expect(within(tree).getByText('Vehicle')).toBeTruthy();
  fireEvent.change(search, { target: { value: 'oo:PhysicalAsset' } });
  expect(within(tree).getByText('Physical asset')).toBeTruthy();
  fireEvent.change(search, { target: { value: 'zzz-no-match' } });
  expect(within(tree).queryByText('Vehicle')).toBeNull();
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

it('uses the English part of a mixed catalog label and includes descendant workspace chains', async () => {
  vi.stubGlobal('fetch', mockFetch({...payload,
    types: [...payload.types.map(row => row.id==='oo:Vehicle'?{...row,name:'载具 / Vehicle'}:row), {id:'aas:TrafficUAV',name:{'en-US':'Traffic UAV'},parents:['oo:Vehicle'],abstract:false,count:2,description:''}],
    chains:[{id:'traffic.survey',name:'Survey response',definition:{roles:{actor:{type:'aas:TrafficUAV'}}}}],
  }));
  renderPage();
  await screen.findByRole('heading',{name:'Vehicle'});
  expect(screen.queryByText('载具 / Vehicle')).toBeNull();
  expect(screen.getByText('Survey response')).toBeTruthy();
});
