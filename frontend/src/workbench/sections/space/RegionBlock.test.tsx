import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest';
import { RegionBlock } from './RegionBlock';
import { originShiftM, validateRegion } from './model';
import { forceSvgRenderer } from './leafletTestSetup';
import type { SectionProps } from '../types';
import { ApiError, WorkbenchApi } from '../../api';

beforeAll(() => { forceSvgRenderer(); });

const savedProject = { id: 'w1', revision: 7 } as any;

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

afterEach(cleanup);

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
    expect(request).toHaveBeenCalledTimes(3); // 2 catalog reads (mount + extract selection) + the rejected dry run
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
      attribution: '© OpenStreetMap contributors · ODbL', roads: [{ id: 'r' }],
      diagnostics: ['crop note'],
      geojson: { features: [{ properties: { height_source: 'osm.height' } }, { properties: { height_source: 'osm.building:levels * authored level_height_m' } }, { properties: {} }] },
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
