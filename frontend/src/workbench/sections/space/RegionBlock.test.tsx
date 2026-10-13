import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import * as Leaflet from 'leaflet';
import { RegionBlock } from './RegionBlock';
import { originShiftM, validateRegion } from './model';
import { captureMaps, forceSvgRenderer, mapLayers } from './leafletTestSetup';
import { APPLIED_REGION, BASEMAP, COVERAGE, DRAFT, PREVIEW, SCENE, WGS_RINGS } from './mapTestFixtures';
import type { SectionProps } from '../types';
import { ApiError, WorkbenchApi } from '../../api';

beforeAll(() => { forceSvgRenderer(); });
let maps: Leaflet.Map[];
beforeEach(() => { maps = captureMaps(); });

const savedProject = { id: 'w1', revision: 3 } as any;

/** Route mocks by URL prefix instead of call order: every request for the same
 *  resource gets the same answer, so mount-order changes cannot skew assertions. */
function mockApi(routes: Record<string, unknown | ((body?: unknown) => unknown)>, request = vi.fn()) {
  request.mockImplementation(async (url: string, init?: { body?: unknown }) => {
    for (const [prefix, answer] of Object.entries(routes)) {
      if (url.startsWith(prefix)) return typeof answer === 'function' ? (answer as (body?: unknown) => unknown)(init?.body) : answer;
    }
    throw new Error(`mockApi: unexpected request ${url}`);
  });
  return Object.assign(new WorkbenchApi('http://api.test'), { request });
}

function makeProps(overrides: Partial<Record<keyof SectionProps, unknown>> = {}): SectionProps {
  return {
    api: mockApi({ '/v1/studio/catalog': { extracts: [{ id: 'wujiaochang' }] } }),
    studioApi: { request: vi.fn() } as any,
    project: { id: 'w1', revision: 3, scenario: {} } as any,
    draft: {}, edit: vi.fn(), replaceDraft: vi.fn(), saveDraft: vi.fn(async () => savedProject),
    onProject: vi.fn(), reportValidity: vi.fn(), zh: false, locale: 'en-US', L: (en: string) => en,
    issues: [], focus: undefined, ...overrides,
  } as unknown as SectionProps;
}

const fill = (label: string, value: string) => fireEvent.change(screen.getByLabelText(label), { target: { value } });
const FIELD_VALUES = [['West (°)', '121.50'], ['South (°)', '31.30'], ['East (°)', '121.52'], ['North (°)', '31.31'], ['Ground altitude (m)', '20'], ['Level height (m)', '3.5']] as const;
const fillRegion = () => { for (const [label, value] of FIELD_VALUES) fill(label, value); };
const selectExtract = async () => {
  await screen.findByRole('option', { name: /^wujiaochang/ });
  fireEvent.change(screen.getByLabelText('Extract'), { target: { value: 'wujiaochang' } });
};

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

describe('RegionBlock validation gating', () => {
  it('keeps actions disabled until complete, ordered bounds are entered', async () => {
    render(<RegionBlock {...makeProps()} />);
    expect(screen.getByRole('button', { name: 'Preview region' })).toBeDisabled();
    for (const [label, value] of FIELD_VALUES) {
      fill(label, value);
      expect(screen.getByRole('button', { name: 'Preview region' })).toBeDisabled();
    }
    // No extract selected yet: still disabled.
    expect(screen.getByRole('button', { name: 'Preview region' })).toBeDisabled();
    await selectExtract();
    expect(screen.getByRole('button', { name: 'Preview region' })).toBeEnabled();
  });

  it('blocks reversed bounds and out-of-coverage extents next to the field without substituting values', () => {
    expect(validateRegion({ extract: 'x', west: '10', south: '0', east: '9', north: '1', alt: '0', level: '3' }).errors.west).toMatch(/smaller than east/);
    expect(validateRegion({ extract: 'x', west: '0', south: '5', east: '1', north: '4', alt: '0', level: '3' }).errors.south).toMatch(/smaller than north/);
    const inside = validateRegion({ extract: 'x', west: '0.1', south: '0.1', east: '0.9', north: '0.9', alt: '2', level: '3' }, [0, 0, 1, 1]);
    expect(inside.body).toBeTruthy();
    const outside = validateRegion({ extract: 'x', west: '-1', south: '0', east: '0.9', north: '0.9', alt: '2', level: '3' }, [0, 0, 1, 1]);
    expect(outside.body).toBeUndefined();
    expect(outside.errors.west).toMatch(/inside the extract/);
    expect(validateRegion({ extract: 'x', west: '0', south: '0', east: '1', north: '1', alt: '', level: '3' }).errors.alt).toBeTruthy();
    expect(validateRegion({ extract: 'x', west: '0', south: '0', east: '1', north: '1', alt: '1', level: '-2' }).errors.level).toBeTruthy();
  });

  it('computes origin shift in metres', () => {
    expect(originShiftM({ lat: 31.3, lon: 121.5 }, { lat: 31.3, lon: 121.5 })).toBe(0);
    expect(originShiftM({ lat: 31.3, lon: 121.5 }, { lat: 31.302, lon: 121.5 })).toBeGreaterThan(200);
    expect(originShiftM({ lat: 31.3, lon: 121.5 }, { lat: 31.3, lon: 121.502 })).toBeGreaterThan(180);
  });
});

describe('RegionBlock preview and apply', () => {
  const previewBody = { extract: 'wujiaochang', bounds: [121.5, 31.3, 121.52, 31.31], alt: 20, level_height_m: 3.5 };
  const preview = {
    extract: 'wujiaochang', bounds: previewBody.bounds, origin: { lat: 31.3, lon: 121.5, alt: 20 },
    previous_origin: { lat: 31.304, lon: 121.505, alt: 0 }, origin_shift_m: 222.1,
    buildings: 3, roads: 1, attribution: '© OpenStreetMap contributors · ODbL',
    height_source: 'osm.height', diagnostics: ['way/1: missing height'], affected_entities: ['marker-1'],
    network_invalidated: true, city_conflict: false,
    geometry: PREVIEW.geometry,
  };
  const appliedProject = { ...savedProject, region: previewBody, scene: { origin: preview.origin } };

  function regionRoutes(request = vi.fn()) {
    return mockApi({
      '/v1/studio/catalog': { extracts: [{ id: 'wujiaochang', bounds: previewBody.bounds }] },
      '/v1/studio/workspaces/w1/region': (body?: unknown) => (body as { dry_run?: boolean })?.dry_run ? { preview } : appliedProject,
      '/v1/studio/workspaces/w1': appliedProject,
    }, request);
  }

  it('requires explicit confirmation when the origin shifts, then saves the draft before applying', async () => {
    const request = vi.fn();
    const api = regionRoutes(request);
    const saveDraft = vi.fn(async () => savedProject);
    const onProject = vi.fn();
    render(<RegionBlock {...makeProps({ api, saveDraft, onProject })} />);
    await selectExtract();
    fillRegion();
    fireEvent.click(screen.getByRole('button', { name: 'Preview region' }));
    await screen.findByTestId('region-review');
    expect(request).toHaveBeenCalledWith('/v1/studio/workspaces/w1/region', { body: { ...previewBody, dry_run: true } });
    expect(screen.getByText(/222\.1 m/)).toBeTruthy();
    expect(screen.getByText(/marker-1/)).toBeTruthy();
    expect(screen.getByText(/road network becomes stale and must be regenerated/)).toBeVisible();
    const apply = screen.getByRole('button', { name: 'Apply region' });
    expect(apply).toBeDisabled(); // shift > 0 without acknowledgement
    fireEvent.click(screen.getByRole('checkbox'));
    expect(apply).toBeEnabled();
    fireEvent.click(apply);
    await waitFor(() => expect(onProject).toHaveBeenCalledWith(appliedProject));
    // The region endpoint accepts these four fields; revision belongs to draft saving.
    expect(request).toHaveBeenCalledWith('/v1/studio/workspaces/w1/region', { body: previewBody });
    expect(saveDraft).toHaveBeenCalledTimes(1);
    // The apply POST must come after the draft was saved.
    const calls = request.mock.calls.map(call => String(call[0]));
    const applyIndex = calls.lastIndexOf('/v1/studio/workspaces/w1/region');
    expect(saveDraft.mock.invocationCallOrder[0]).toBeLessThan(request.mock.invocationCallOrder[applyIndex]);
    // State after the mutation comes from the server (design ③): a project re-fetch.
    expect(request).toHaveBeenCalledWith('/v1/studio/workspaces/w1', { signal: undefined });
  });

  it('marks a previewed region out of date and disables Apply when the inputs change', async () => {
    render(<RegionBlock {...makeProps({ api: regionRoutes() })} />);
    await selectExtract();
    fillRegion();
    fireEvent.click(screen.getByRole('button', { name: 'Preview region' }));
    await screen.findByTestId('region-review');
    // This preview reports an origin shift, so the acknowledgement gates first…
    expect(screen.getByRole('button', { name: 'Apply region' })).toBeDisabled();
    fireEvent.click(screen.getByRole('checkbox'));
    expect(screen.getByRole('button', { name: 'Apply region' })).toBeEnabled();
    // …then any input change marks the preview out of date and disables Apply again.
    fill('Level height (m)', '4');
    expect(screen.getAllByText(/Out of date/).length).toBeGreaterThan(0);
    expect(screen.getByRole('button', { name: 'Apply region' })).toBeDisabled();
    // Preview again with the new input: the stale mark clears. No silent default is ever filled in.
    expect((screen.getByLabelText('Level height (m)') as HTMLInputElement).value).toBe('4');
    fireEvent.click(screen.getByRole('button', { name: 'Preview region' }));
    await waitFor(() => expect(screen.queryAllByText(/Out of date/).length).toBe(0));
    fireEvent.click(screen.getByRole('checkbox'));
    expect(screen.getByRole('button', { name: 'Apply region' })).toBeEnabled();
  });

  it('degrades with a clear message when the server has no preview capability', async () => {
    const request = vi.fn();
    const api = mockApi({
      '/v1/studio/catalog': { extracts: [{ id: 'wujiaochang' }] },
      '/v1/studio/workspaces/w1/region': () => { throw new ApiError(404, 'not found'); },
    }, request);
    render(<RegionBlock {...makeProps({ api })} />);
    await selectExtract();
    fillRegion();
    fireEvent.click(screen.getByRole('button', { name: 'Preview region' }));
    expect(await screen.findByText(/does not support region preview/)).toBeTruthy();
    // Cancellation path: no apply happened.
    expect(screen.queryByTestId('region-review')).toBeNull();
    expect(screen.getByRole('button', { name: 'Apply region' })).toBeDisabled();
    expect(request).toHaveBeenCalledTimes(2); // one catalog read plus the rejected preview
  });

  it('shows the city conflict and clears the city on request', () => {
    const props = makeProps({ draft: { city: 'huangpu' }, edit: vi.fn() });
    (props.project as any).region = { extract: 'wujiaochang', bounds: [0, 0, 1, 1], alt: 0, level_height_m: 3 };
    render(<RegionBlock {...props} />);
    expect(screen.getByTestId('city-conflict').textContent).toContain('mix');
    fireEvent.click(screen.getByTestId('clear-city'));
    expect(props.edit).toHaveBeenCalledWith('/city', undefined);
  });

  it('reports validation issues for /city', () => {
    render(<RegionBlock {...makeProps({ issues: [{ source: 'space', path: '/city', message: 'city conflicts with region', section: 'space' } as any] })} />);
    expect(screen.getByText('city conflicts with region')).toBeTruthy();
  });

  it('shows the persistent server-side region summary with height accounting', () => {
    const props = makeProps();
    (props.project as any).region = { extract: 'wujiaochang', bounds: [121.5, 31.3, 121.52, 31.31], alt: 20, level_height_m: 3.5 };
    (props.project as any).scene = {
      origin: { lat: 31.3, lon: 121.5, alt: 20 }, ground: { width_m: 2406, depth_m: 713 },
      attribution: '© OpenStreetMap contributors · ODbL', roads: [{ id: 'r', points: [[0, 0, 0], [10, 10, 0]] }],
      diagnostics: ['crop note'],
      geojson: SCENE!.geojson,
    };
    render(<RegionBlock {...props} />);
    const summary = screen.getByTestId('region-summary');
    expect(summary.textContent).toContain('wujiaochang');
    expect(summary.textContent).toContain('© OpenStreetMap contributors');
    expect(summary.textContent).toContain('estimated from building:levels');
    expect(summary.textContent).toContain('2D footprints');
    expect(summary.textContent).toContain('crop note');
  });

  it('shows the server network state: not generated / stale before a region / stale after a region change', async () => {
    // Without a region the road-network step is disabled with an inline reason.
    render(<RegionBlock {...makeProps()} />);
    await screen.findByRole('option', { name: /^wujiaochang/ });
    expect(screen.getByTestId('network-summary').textContent).toContain('Not generated');
    expect(screen.getByTestId('generate-network')).toBeDisabled();
    expect(screen.getByText(/Apply a region first/)).toBeTruthy();
    cleanup();
    // A generated network the server no longer backs with network.net.xml → out of date.
    const props = makeProps();
    (props.project as any).region = { extract: 'wujiaochang', bounds: [0, 0, 1, 1], alt: 0, level_height_m: 3 };
    (props.project as any).network = { status: 'generated', edge_count: 4, lane_count: 8 };
    (props.project as any).capabilities = { lowalt: false, region: true, network: false, city: false };
    render(<RegionBlock {...props} />);
    expect(screen.getByTestId('network-summary').textContent).toContain('Out of date');
  });

  it('generates the road network after a region is applied and re-reads the server state', async () => {
    const generated = { status: 'generated', edge_count: 1, lane_count: 2, location: { projParameter: '+proj=tmerc' } };
    const refreshed = { id: 'w1', region: { extract: 'wujiaochang', bounds: [0, 0, 1, 1], alt: 0, level_height_m: 3 }, network: generated, capabilities: { lowalt: false, region: true, network: true, city: false } };
    const request = vi.fn();
    const api = mockApi({
      '/v1/studio/catalog': { extracts: [{ id: 'wujiaochang' }] },
      '/v1/studio/workspaces/w1/network': generated,
      '/v1/studio/workspaces/w1': refreshed,
    }, request);
    const onProject = vi.fn();
    const props = makeProps({ api, onProject });
    (props.project as any).region = refreshed.region;
    (props.project as any).capabilities = { lowalt: false, region: true, network: false, city: false };
    render(<RegionBlock {...props} />);
    await screen.findByRole('option', { name: /^wujiaochang/ });
    expect(screen.getByTestId('generate-network')).toBeEnabled();
    fireEvent.click(screen.getByTestId('generate-network'));
    const summary = await screen.findByTestId('network-summary');
    await waitFor(() => expect(onProject).toHaveBeenCalledWith(refreshed));
    expect(summary.textContent).toContain('1');
    expect(summary.textContent).toContain('not a traffic run');
    expect(request).toHaveBeenCalledWith('/v1/studio/workspaces/w1/network', { body: {} });
  });

  it('prefers the server field network_state over the capability inference for the road-network step', () => {
    // Server says stale even though the capability inference would call it generated.
    const props = makeProps();
    (props.project as any).region = { extract: 'wujiaochang', bounds: [0, 0, 1, 1], alt: 0, level_height_m: 3 };
    (props.project as any).network = { status: 'generated', edge_count: 4, lane_count: 8 };
    (props.project as any).network_state = 'stale';
    (props.project as any).capabilities = { lowalt: false, region: true, network: true, city: false };
    render(<RegionBlock {...props} />);
    expect(screen.getByTestId('network-summary').textContent).toContain('stale');
    expect(screen.getByTestId('network-summary').textContent).toContain('generate again');
  });

  it('falls back to the capability inference when network_state is absent', () => {
    const props = makeProps();
    (props.project as any).region = { extract: 'wujiaochang', bounds: [0, 0, 1, 1], alt: 0, level_height_m: 3 };
    (props.project as any).network = { status: 'generated', edge_count: 4, lane_count: 8 };
    (props.project as any).capabilities = { lowalt: false, region: true, network: true, city: false };
    render(<RegionBlock {...props} />);
    expect(screen.getByTestId('network-summary').textContent).toContain('generated');
    expect(screen.queryByText(/generate again/)).toBeNull();
  });

  it('honours network_state "none" from the server even with leftover network data', () => {
    const props = makeProps();
    (props.project as any).region = { extract: 'wujiaochang', bounds: [0, 0, 1, 1], alt: 0, level_height_m: 3 };
    (props.project as any).network = { status: 'generated', edge_count: 4 };
    (props.project as any).network_state = 'none';
    render(<RegionBlock {...props} />);
    expect(screen.getByTestId('network-summary').textContent).toContain('Not generated');
    expect(screen.queryByText(/generate again/)).toBeNull();
  });
});

describe('round 2 Region form and review regressions', () => {
  const project = { ...savedProject, scenario: DRAFT, region: APPLIED_REGION, scene: SCENE };
  const catalog = { basemap: BASEMAP, extracts: [{ id: 'wujiaochang', bounds: COVERAGE }] };

  it('hydrates applied extract/bounds/25 m altitude/6 m level immediately and survives reopen and same-extract selection (T1-004 / T1-R2-008)', async () => {
    const crop = [121.51, 31.302, 121.519, 31.305];
    const props = makeProps({ project: { ...project, region: { ...APPLIED_REGION, bounds: crop } }, draft: DRAFT,
      api: mockApi({ '/v1/studio/catalog': catalog }) });
    const verify = () => {
      expect(screen.getByLabelText('Extract')).toHaveValue('wujiaochang');
      expect(screen.getByLabelText('West (°)')).toHaveValue('121.51');
      expect(screen.getByLabelText('South (°)')).toHaveValue('31.302');
      expect(screen.getByLabelText('East (°)')).toHaveValue('121.519');
      expect(screen.getByLabelText('North (°)')).toHaveValue('31.305');
      expect(screen.getByLabelText('Ground altitude (m)')).toHaveValue('25');
      expect(screen.getByLabelText('Level height (m)')).toHaveValue('6');
      expect(screen.queryByText('Map unavailable — bounds can still be typed')).toBeNull();
    };
    const view = render(<RegionBlock {...props} />);
    verify();
    await waitFor(() => expect(mapLayers(maps[0]).some(layer => layer instanceof Leaflet.TileLayer)).toBe(true));
    fireEvent.change(screen.getByLabelText('Extract'), { target: { value: 'wujiaochang' } });
    verify();
    view.unmount();
    render(<RegionBlock {...props} />);
    verify();
  });

  it('updates from a genuine applied-region change and preserves edits on unrelated refreshes', () => {
    const props = makeProps({ project, draft: DRAFT });
    const view = render(<RegionBlock {...props} />);
    fill('Ground altitude (m)', '30');
    view.rerender(<RegionBlock {...props} project={{ ...props.project, region: { ...APPLIED_REGION } }} />);
    expect(screen.getByLabelText('Ground altitude (m)')).toHaveValue('30');
    view.rerender(<RegionBlock {...props} project={{ ...props.project, revision: 4, region: { ...APPLIED_REGION, alt: 40, level_height_m: 8 } }} />);
    expect(screen.getByLabelText('Ground altitude (m)')).toHaveValue('40');
    expect(screen.getByLabelText('Level height (m)')).toHaveValue('8');
  });

  it('passes real preview buildings/road records and the server truncation flag through to review (T3-R2-003 / T4-R2-005 / T2-R2-013)', async () => {
    const props = makeProps({ project, draft: DRAFT, api: mockApi({ '/v1/studio/catalog': catalog,
      '/v1/studio/workspaces/w1/region': { preview: { ...PREVIEW, geometry: { ...PREVIEW.geometry!, truncated: true } } } }) });
    render(<RegionBlock {...props} />);
    fireEvent.click(screen.getByRole('button', { name: 'Preview region' }));
    await screen.findByTestId('region-review');
    const buildings = mapLayers(maps[0]).filter((layer): layer is Leaflet.Polygon => layer instanceof Leaflet.Polygon && !(layer instanceof Leaflet.Rectangle));
    const roads = mapLayers(maps[0]).filter(layer => layer instanceof Leaflet.Polyline && !(layer instanceof Leaflet.Polygon));
    expect(buildings).toHaveLength(3);
    expect(roads).toHaveLength(1);
    const first = (buildings[0].getLatLngs() as Leaflet.LatLng[][])[0][0];
    expect(first.lng).toBeCloseTo(WGS_RINGS[0][0][0], 10);
    expect(first.lat).toBeCloseTo(WGS_RINGS[0][0][1], 10);
    expect(screen.getByText(/Preview truncated/)).toBeVisible();
    expect(screen.getByText('OSM height; building levels')).toBeVisible();
    expect(screen.queryByText(/OSM 高度；建筑层数/)).toBeNull();
  });

  it('writes map-drawn W/S/E/N back to the actual form and restores panning (T1-R2-003 / T2-R2-006)', async () => {
    render(<RegionBlock {...makeProps({ project, api: mockApi({ '/v1/studio/catalog': catalog }) })} />);
    await waitFor(() => expect(mapLayers(maps[0]).some(layer => layer instanceof Leaflet.TileLayer)).toBe(true));
    const map = maps[0], before = map.getCenter();
    fireEvent.click(screen.getByRole('button', { name: 'Draw area' }));
    expect(map.dragging.enabled()).toBe(false);
    act(() => {
      map.fire('mousedown', { latlng: Leaflet.latLng(31.302, 121.505) });
      map.fire('mousemove', { latlng: Leaflet.latLng(31.305, 121.52) });
      map.fire('mouseup', { latlng: Leaflet.latLng(31.305, 121.52) });
    });
    expect(screen.getByLabelText('West (°)')).toHaveValue('121.505000');
    expect(screen.getByLabelText('South (°)')).toHaveValue('31.302000');
    expect(screen.getByLabelText('East (°)')).toHaveValue('121.520000');
    expect(screen.getByLabelText('North (°)')).toHaveValue('31.305000');
    expect(map.getCenter()).toEqual(before);
    expect(map.dragging.enabled()).toBe(true);
  });

  it('keeps an unchanged review through rerenders/numeric formatting and invalidates a real base-revision change', async () => {
    const props = makeProps({ project, draft: DRAFT, api: mockApi({ '/v1/studio/catalog': catalog,
      '/v1/studio/workspaces/w1/region': { preview: PREVIEW } }) });
    const view = render(<RegionBlock {...props} />);
    fireEvent.click(screen.getByRole('button', { name: 'Preview region' }));
    await screen.findByTestId('region-review');
    fireEvent.click(screen.getByRole('checkbox'));
    fill('Ground altitude (m)', '25.0');
    view.rerender(<RegionBlock {...props} project={{ ...props.project }} draft={{ ...DRAFT }} />);
    expect(screen.getByRole('button', { name: 'Apply region' })).toBeEnabled();
    view.rerender(<RegionBlock {...props} project={{ ...props.project, revision: 4 }} />);
    expect(screen.getByRole('button', { name: 'Apply region' })).toBeDisabled();
  });

  it('does not apply a review if saving other edits moved its base revision', async () => {
    const request = vi.fn();
    const api = mockApi({ '/v1/studio/catalog': catalog, '/v1/studio/workspaces/w1/region': { preview: PREVIEW } }, request);
    render(<RegionBlock {...makeProps({ project, api, saveDraft: vi.fn(async () => ({ ...project, revision: 4 })) })} />);
    fireEvent.click(screen.getByRole('button', { name: 'Preview region' }));
    await screen.findByTestId('region-review');
    fireEvent.click(screen.getByRole('checkbox'));
    fireEvent.click(screen.getByRole('button', { name: 'Apply region' }));
    expect(await screen.findByText(/draft changed while saving/)).toBeVisible();
    const regionCalls = request.mock.calls.filter(([url]) => url.endsWith('/region'));
    expect(regionCalls).toHaveLength(1);
    expect(regionCalls[0][1].body.dry_run).toBe(true);
  });

  it('shows catalog counts, uses the richer extract on a new project, and leaves absent counts absent (D10)', async () => {
    const api = mockApi({ '/v1/studio/catalog': { basemap: BASEMAP, extracts: [
      { id: 'wujiaochang', label: 'Wujiaochang', buildings: 3, roads: 1, bounds: COVERAGE },
      { id: 'huangpu-lite', label: 'Huangpu lite', buildings: 414, roads: 120, bounds: [121.47, 31.2, 121.5, 31.24] },
      { id: 'other', label: 'Other extract' },
    ] } });
    render(<RegionBlock {...makeProps({ api })} />);
    expect(await screen.findByRole('option', { name: 'Huangpu lite — 414 buildings · 120 roads' })).toBeVisible();
    expect(screen.getByRole('option', { name: 'Wujiaochang — 3 buildings · 1 roads' })).toBeVisible();
    expect(screen.getByRole('option', { name: 'Other extract' })).toBeVisible();
    await waitFor(() => expect(screen.getByLabelText('Extract')).toHaveValue('huangpu-lite'));
    expect(screen.getByLabelText('West (°)')).toHaveValue('121.47');
  });
});
