import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import {TemplateApi} from './api';
import { MemoryRouter,useLocation } from 'react-router-dom';
import { stringifyLossless } from '../feeds/lossless-json';
import TemplateGallery from './TemplateGallery';
import CustomizeTemplate from './CustomizeTemplate';
import Welcome, { WELCOME_EVENT } from './Welcome';
import { CATEGORY_STYLE, CategoryIcon, categoryMetadata } from './categories';
import type { ScenarioTemplate } from './api';

afterEach(() => { cleanup(); vi.restoreAllMocks(); window.localStorage.clear(); });

function Destination(){const location=useLocation();return <output data-testid="destination">{location.pathname}{location.search}</output>;}

const TEMPLATE: ScenarioTemplate = {
  format: 'aeroagentsim.template/v1',
  id: 'parcel-delivery',
  title: 'Urban parcel delivery',
  summary: 'Drones deliver parcels from two vertiports to customers across the district.',
  category: 'logistics',
  difficulty: 'beginner',
  duration_s: 300,
  scenario: 'scenario.yaml',
  observe: ['Orders appear over time and are assigned to the nearest free drone.'],
  try: ['Switch the allocation algorithm from nearest to auction and compare delivery time.'],
  knobs: [
    { id: 'fleet_size', label: 'Number of drones', type: 'integer', default: 6, min: 1, max: 40, pointer: '/generators/fleet/count' },
    { id: 'allocator', label: 'Task allocation algorithm', type: 'choice', default: 'nearest', options: ['nearest', 'auction', 'hungarian', 'llm'], pointer: '/algorithms/allocation' },
  ],
  kpis: ['tasks_completed', 'mean_task_latency_s'],
};

function fetchMock(routes: Record<string, unknown>, failures: Record<string, number> = {}) {
  return vi.fn(async (input: RequestInfo | URL, init?:RequestInit) => {
    const url = String(input);
    for (const [path, body] of Object.entries(routes)) {
      if (url.endsWith(path)) {
        if (failures[path]) {
          failures[path] -= 1;
          return new Response('{"detail":"boom"}', { status: failures[path] >= 0 && failures[path] + 1 === 0 ? 500 : 404, headers: { 'Content-Type': 'application/json' } });
        }
        return new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } });
      }
    }
    return new Response('not found', { status: 404 });
  });
}

it('renders real templates from the API, filters by category and starts a run with default knobs', async () => {
  const fetchSpy = fetchMock({
    '/v1/templates': [TEMPLATE],
    '/v1/templates/parcel-delivery/runs': { run_id: 'run-123', workspace_id: 'ws-9' },
  });
  vi.stubGlobal('fetch', fetchSpy);
  render(<MemoryRouter initialEntries={['/?api=http%3A%2F%2Fapi']}><TemplateGallery apiBase="http://api" /><Destination/></MemoryRouter>);

  const card = await screen.findByTestId('template-parcel-delivery');
  expect(screen.getByText('Urban parcel delivery')).toBeInTheDocument();
  expect(screen.getByTestId('filter-all')).toHaveTextContent('All (1)');
  expect(screen.getByTestId('filter-logistics')).toBeInTheDocument();

  // Primary Run: a real request shaped like POST /v1/templates/{id}/runs with defaults.
  fireEvent.click(screen.getByTestId('run-template-parcel-delivery'));
  await waitFor(()=>expect(screen.getByTestId('destination')).toHaveTextContent('/runs/run-123?mode=live&view=simple&template=parcel-delivery'));
  const call = (fetchSpy.mock.calls as unknown as Array<[RequestInfo | URL, RequestInit | undefined]>).find(([, i]) => String(i?.method) === 'POST');
  const url = String(call?.[0]);
  const init = (call?.[1] ?? {}) as RequestInit;
  expect(url).toBe('http://api/v1/templates/parcel-delivery/runs');
  expect(JSON.parse(String(init.body))).toEqual(JSON.parse(stringifyLossless({ knobs: { fleet_size: 6, allocator: 'nearest' } })));
});

it('shows a truthful error with a Studio fallback when templates are unavailable (including 404), never a fake catalog', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => new Response('nope', { status: 404 })));
  render(<MemoryRouter><TemplateGallery apiBase="http://api" /><Destination/></MemoryRouter>);
  const alert = await screen.findByRole('alert');
  expect(alert).toHaveTextContent('Could not load scenario templates');
  expect(screen.queryByTestId('template-parcel-delivery')).toBeNull();
  expect(screen.getByRole('link', { name: 'Go to Studio' })).toHaveAttribute('href', '/studio?api=http%3A%2F%2Fapi');
});

it('posts edited settings with their declared numeric type',async()=>{
 const fetchSpy=fetchMock({'/v1/templates/parcel-delivery/runs':{run_id:'run-2'}});vi.stubGlobal('fetch',fetchSpy);
 render(<MemoryRouter><CustomizeTemplate template={TEMPLATE} api={new TemplateApi('http://api')} apiBase="http://api"/><Destination/></MemoryRouter>);
 const number=screen.getByRole('spinbutton',{name:'Number of drones'});fireEvent.change(number,{target:{value:'10'}});fireEvent.blur(number);
 fireEvent.click(screen.getByTestId('custom-run-parcel-delivery'));
 await waitFor(()=>expect(screen.getByTestId('destination')).toHaveTextContent('/runs/run-2'));
 const post=fetchSpy.mock.calls.find(([,init])=>init?.method==='POST');expect(JSON.parse(String(post?.[1]?.body))).toEqual({knobs:{fleet_size:10,allocator:'nearest'}});
});

it('shows the welcome once, dismisses to localStorage and reopens on the exported event', async () => {
  render(<MemoryRouter><Welcome /></MemoryRouter>);
  expect(await screen.findByTestId('welcome-panel')).toBeInTheDocument();
  expect(window.localStorage.getItem('aas:welcome-dismissed')).toBeNull();
  fireEvent.click(screen.getByTestId('welcome-close'));
  await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  expect(window.localStorage.getItem('aas:welcome-dismissed')).toBe('1');

  fireEvent(document,new CustomEvent(WELCOME_EVENT));
  expect(await screen.findByTestId('welcome-panel')).toBeInTheDocument();
});

it('exposes a stable colourblind-safe category palette shared with the event feed', () => {
  expect(categoryMetadata('logistics')).toEqual(CATEGORY_STYLE.logistics);
  expect(CATEGORY_STYLE.emergency.label).toBe('Emergency');
  const { container } = render(<CategoryIcon category="airspace" size={18} />);
  expect(container.querySelector('svg[aria-hidden="true"]')).not.toBeNull();
});
