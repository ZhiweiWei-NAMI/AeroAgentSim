import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { RegionBlock } from './RegionBlock';
import { originShiftM, validateRegion } from './model';
import type { SectionProps } from '../types';

const savedProject = { id: 'w1', revision: 7 } as any;

function makeProps(overrides: Partial<Record<keyof SectionProps, unknown>> = {}): SectionProps {
  return {
    api: { request: vi.fn() } as any, studioApi: { request: vi.fn() } as any,
    project: { id: 'w1', revision: 3, scenario: {} } as any,
    draft: {}, edit: vi.fn(), replaceDraft: vi.fn(), saveDraft: vi.fn(async () => savedProject),
    onProject: vi.fn(), reportValidity: vi.fn(), zh: false, locale: 'en-US', L: (en: string) => en,
    issues: [], focus: undefined, ...overrides,
  } as unknown as SectionProps;
}

const fill = (label: string, value: string) => fireEvent.change(screen.getByLabelText(label), { target: { value } });
const FIELDS = ['West (°)', 'South (°)', 'East (°)', 'North (°)', 'Ground altitude (m)', 'Level height (m)'];

afterEach(cleanup);

describe('RegionBlock validation gating', () => {
  it('keeps actions disabled until complete, ordered bounds are entered', () => {
    render(<RegionBlock {...makeProps()} />);
    for (const label of FIELDS) expect(screen.getByRole('button', { name: 'Preview region' })).toBeDisabled();
    fill('West (°)', '121.50'); fill('South (°)', '31.30'); fill('East (°)', '121.52'); fill('North (°)', '31.31');
    fill('Ground altitude (m)', '20'); fill('Level height (m)', '3.5');
    // No extract selected yet: still disabled.
    expect(screen.getByRole('button', { name: 'Preview region' })).toBeDisabled();
    fireEvent.change(screen.getByLabelText('Extract'), { target: { value: 'wujiaochang' } });
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

  it('requires explicit confirmation when the origin shifts, then saves the draft before applying', async () => {
    const request = vi.fn()
      .mockResolvedValueOnce({ extracts: [{ id: 'wujiaochang', bounds: previewBody.bounds }] }) // catalog
      .mockResolvedValueOnce({ preview }) // dry run
      .mockResolvedValueOnce({ id: 'w1', region: previewBody, scene: { origin: preview.origin } }); // apply
    const saveDraft = vi.fn(async () => savedProject);
    const onProject = vi.fn();
    const props = makeProps({ api: { request } as any, saveDraft, onProject });
    render(<RegionBlock {...props} />);
    fireEvent.change(screen.getByLabelText('Extract'), { target: { value: 'wujiaochang' } });
    for (const [label, value] of [['West (°)', '121.50'], ['South (°)', '31.30'], ['East (°)', '121.52'], ['North (°)', '31.31'], ['Ground altitude (m)', '20'], ['Level height (m)', '3.5']]) fill(label as string, value as string);
    fireEvent.click(screen.getByRole('button', { name: 'Preview region' }));
    await screen.findByTestId('region-review');
    expect(request.mock.calls[1][1].body).toMatchObject({ dry_run: true });
    expect(screen.getByText(/222\.1 m/)).toBeTruthy();
    expect(screen.getByText(/marker-1/)).toBeTruthy();
    expect(screen.getByText(/stale/)).toBeTruthy();
    const apply = screen.getByRole('button', { name: 'Apply region' });
    expect(apply).toBeDisabled(); // shift > 0 without acknowledgement
    fireEvent.click(screen.getByRole('checkbox'));
    expect(apply).toBeEnabled();
    fireEvent.click(apply);
    await waitFor(() => expect(request.mock.calls[2][1].body).toMatchObject({ revision: 7 }));
    expect(request.mock.calls[2][1].body).not.toHaveProperty('dry_run');
    expect(saveDraft).toHaveBeenCalledTimes(1);
    expect(onProject).toHaveBeenCalledWith(expect.objectContaining({ id: 'w1' }));
  });

  it('degrades with a clear message when the server has no preview capability', async () => {
    const { ApiError } = await import('../../api');
    const request = vi.fn()
      .mockResolvedValueOnce({ extracts: [{ id: 'wujiaochang' }] })
      .mockRejectedValueOnce(new ApiError(404, 'not found'));
    render(<RegionBlock {...makeProps({ api: { request } as any })} />);
    fireEvent.change(screen.getByLabelText('Extract'), { target: { value: 'wujiaochang' } });
    for (const [label, value] of [['West (°)', '121.50'], ['South (°)', '31.30'], ['East (°)', '121.52'], ['North (°)', '31.31'], ['Ground altitude (m)', '20'], ['Level height (m)', '3.5']]) fill(label as string, value as string);
    fireEvent.click(screen.getByRole('button', { name: 'Preview region' }));
    expect(await screen.findByText(/does not support region preview/)).toBeTruthy();
    // Cancellation path: no apply happened.
    expect(screen.queryByTestId('region-review')).toBeNull();
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

  it('shows network generation result and states it is not a traffic run', async () => {
    const request = vi.fn()
      .mockResolvedValueOnce({ extracts: [{ id: 'wujiaochang' }] })
      .mockResolvedValueOnce({ status: 'generated', edge_count: 1, lane_count: 2, location: { projParameter: '+proj=tmerc' } })
      .mockResolvedValueOnce({ id: 'w1', network: { edge_count: 1 } });
    const props = makeProps({ api: { request } as any });
    (props.project as any).region = { extract: 'wujiaochang', bounds: [0, 0, 1, 1], alt: 0, level_height_m: 3 };
    render(<RegionBlock {...props} />);
    fill('West (°)', '0'); fill('South (°)', '0'); fill('East (°)', '1'); fill('North (°)', '1'); fill('Ground altitude (m)', '0'); fill('Level height (m)', '3');
    fireEvent.click(screen.getByRole('button', { name: 'Generate road network' }));
    const summary = await screen.findByTestId('network-summary');
    expect(summary.textContent).toContain('1');
    expect(summary.textContent).toContain('not a traffic run');
  });
});
